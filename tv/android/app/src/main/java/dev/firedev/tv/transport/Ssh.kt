package dev.firedev.tv.transport

import net.i2p.crypto.eddsa.EdDSAPrivateKey
import net.i2p.crypto.eddsa.EdDSAPublicKey
import net.i2p.crypto.eddsa.spec.EdDSANamedCurveTable
import net.i2p.crypto.eddsa.spec.EdDSAPrivateKeySpec
import net.i2p.crypto.eddsa.spec.EdDSAPublicKeySpec
import net.schmizz.sshj.DefaultConfig
import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.IOUtils
import net.schmizz.sshj.transport.verification.HostKeyVerifier
import net.schmizz.sshj.userauth.keyprovider.KeyPairWrapper
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.security.KeyPair
import java.security.MessageDigest
import java.security.PublicKey
import java.security.SecureRandom
import java.util.concurrent.TimeUnit

/** The TV's own SSH key (Ed25519). Only the 32-byte seed is stored, by the app, on the TV. */
object TvKey {
    private val spec = EdDSANamedCurveTable.getByName(EdDSANamedCurveTable.ED_25519)

    fun newSeed(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }

    fun fromSeed(seed: ByteArray): KeyPair {
        val priv = EdDSAPrivateKey(EdDSAPrivateKeySpec(seed, spec))
        val pub = EdDSAPublicKey(EdDSAPublicKeySpec(priv.a, spec))
        return KeyPair(pub, priv)
    }

    /** The line for ~/.ssh/authorized_keys. */
    fun publicLine(pair: KeyPair, comment: String): String {
        val pub = (pair.public as EdDSAPublicKey).abyte
        val blob = ByteArrayOutputStream().apply { sshString("ssh-ed25519".toByteArray()); sshString(pub) }.toByteArray()
        return "ssh-ed25519 ${b64(blob)} $comment"
    }

    private fun ByteArrayOutputStream.sshString(b: ByteArray) {
        write(byteArrayOf((b.size ushr 24).toByte(), (b.size ushr 16).toByte(), (b.size ushr 8).toByte(), b.size.toByte()))
        write(b)
    }
}

/** OpenSSH-style host key fingerprint: SHA256:<base64, no padding>, as `ssh-keygen -lf` prints it. */
fun hostFingerprint(key: PublicKey): String {
    val blob = Buffer.PlainBuffer().putPublicKey(key).compactData
    return "SHA256:" + b64(MessageDigest.getInstance("SHA-256").digest(blob)).trimEnd('=')
}

internal fun b64(bytes: ByteArray): String {
    val t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    val sb = StringBuilder()
    var i = 0
    while (i < bytes.size) {
        val b0 = bytes[i].toInt() and 0xff
        val b1 = if (i + 1 < bytes.size) bytes[i + 1].toInt() and 0xff else 0
        val b2 = if (i + 2 < bytes.size) bytes[i + 2].toInt() and 0xff else 0
        sb.append(t[b0 shr 2]).append(t[((b0 and 3) shl 4) or (b1 shr 4)])
        sb.append(if (i + 1 < bytes.size) t[((b1 and 15) shl 2) or (b2 shr 6)] else '=')
        sb.append(if (i + 2 < bytes.size) t[b2 and 63] else '=')
        i += 3
    }
    return sb.toString()
}

class HostKeyMismatch(val expected: String, val got: String) :
    Exception("The server's host key changed (expected $expected, got $got). If you didn't reinstall the server, someone may be intercepting the connection.")

class UnknownHostKey(val fingerprint: String) : Exception("New server: confirm its host key $fingerprint")

/**
 * The TV's SSH connection to the user's own server: host key pinned (confirmed by the user the first time), the TV's
 * key for login, keepalives, and reconnecting by itself when the network comes back. Everything the TV interface
 * needs goes through [direct] channels to 127.0.0.1 ports on the server; nothing listens on the server's network.
 */
class SshLink(val host: String, val port: Int, val user: String, private val key: KeyPair, @Volatile var hostKeyPin: String?) {
    private var client: SSHClient? = null

    /** The host key seen on the last attempt (to show the user when it isn't pinned yet). */
    @Volatile var seenHostKey: String? = null
        private set

    @Synchronized
    fun ensure(password: String? = null): SSHClient {
        client?.let { if (it.isConnected && it.isAuthenticated) return it }
        client?.let { runCatching { it.disconnect() } }
        client = null
        val c = SSHClient(DefaultConfig())
        c.connectTimeout = 10_000
        c.timeout = 0
        c.addHostKeyVerifier(object : HostKeyVerifier {
            override fun verify(hostname: String, port: Int, key: PublicKey): Boolean {
                val fp = hostFingerprint(key)
                seenHostKey = fp
                val pin = hostKeyPin ?: throw UnknownHostKey(fp)
                if (pin != fp) throw HostKeyMismatch(pin, fp)
                return true
            }
            override fun findExistingAlgorithms(hostname: String, port: Int): List<String> = emptyList()
        })
        try {
            c.connect(host, port)
        } catch (e: Exception) {
            runCatching { c.disconnect() }
            throw unwrap(e)
        }
        c.connection.keepAlive.keepAliveInterval = 15
        try {
            if (password != null) c.authPassword(user, password) else c.authPublickey(user, KeyPairWrapper(key))
        } catch (e: Exception) {
            runCatching { c.disconnect() }
            throw Exception(if (password != null) "The server refused that password." else
                "The server didn't accept the TV's key. Add it to ~/.ssh/authorized_keys for $user (the TV shows it).", e)
        }
        client = c
        return c
    }

    /** Runs a command in a login shell on the server (so ~/.local/bin and the like are on PATH). */
    fun exec(command: String, timeoutSeconds: Long = 60, password: String? = null): Pair<Int, String> {
        val c = ensure(password)
        c.startSession().use { s ->
            val cmd = s.exec("bash -lc " + shellQuote(command))
            val out = IOUtils.readFully(cmd.inputStream).toString(Charsets.UTF_8.name())
            val err = IOUtils.readFully(cmd.errorStream).toString(Charsets.UTF_8.name())
            cmd.join(timeoutSeconds, TimeUnit.SECONDS)
            return (cmd.exitStatus ?: -1) to (out + if (err.isNotBlank()) "\n" + err else "")
        }
    }

    /** A channel to 127.0.0.1:[remotePort] on the server, for the relay. */
    fun direct(remotePort: Int): Relay.Upstream {
        val ch = ensure().newDirectConnection("127.0.0.1", remotePort)
        return object : Relay.Upstream {
            override val input: InputStream = ch.inputStream
            override val output: OutputStream = ch.outputStream
            override fun close() { runCatching { ch.close() } }
        }
    }

    @Synchronized
    fun close() {
        client?.let { runCatching { it.disconnect() } }
        client = null
    }

    private fun unwrap(e: Exception): Exception {
        var t: Throwable? = e
        while (t != null) {
            if (t is UnknownHostKey || t is HostKeyMismatch) return t as Exception
            t = t.cause
        }
        return Exception("Couldn't reach $host:$port (${e.message ?: e.javaClass.simpleName})", e)
    }

    companion object {
        fun shellQuote(s: String) = "'" + s.replace("'", "'\\''") + "'"
    }
}

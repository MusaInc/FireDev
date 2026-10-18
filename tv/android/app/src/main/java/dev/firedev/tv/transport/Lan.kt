package dev.firedev.tv.transport

import java.net.InetSocketAddress
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/**
 * TLS to a FireDev computer on the LAN with its self-signed certificate pinned.
 *
 * The first connection (before pairing) accepts the certificate it is shown and remembers its fingerprint; pairing
 * then proves it: the code the TV shows is [pairCode] of that fingerprint, and the computer only accepts the code made
 * from its own certificate, so a man in the middle can't be paired. After pairing every connection must match the pin.
 */
class PinnedTls(pin: String?) {
    @Volatile var pin: String? = pin?.lowercase()

    /** The fingerprint of the last certificate seen (what pairing checks). */
    @Volatile var seen: String? = null
        private set

    fun connect(host: String, port: Int, timeoutMs: Int = 8000): SSLSocket {
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = throw CertificateException("not a server")
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val fp = sha256Hex(chain[0].encoded)
                seen = fp
                val want = pin
                if (want != null && want != fp) throw CertificateException("This computer's certificate changed. Pair it again if you reinstalled FireDev there.")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(trust), SecureRandom())
        val s = ctx.socketFactory.createSocket() as SSLSocket
        s.tcpNoDelay = true
        s.connect(InetSocketAddress(host, port), timeoutMs)
        s.soTimeout = 0
        s.startHandshake()
        return s
    }

    companion object {
        fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        /** The same derivation as the computer's auth.bound_code. */
        fun pairCode(fingerprint: String, claim: String, nonce: String): String {
            val hex = sha256Hex("firedev-pair:${fingerprint.lowercase()}:$claim:$nonce".toByteArray())
            return "%06d".format(java.lang.Long.parseLong(hex.substring(0, 15), 16) % 1_000_000)
        }
    }
}

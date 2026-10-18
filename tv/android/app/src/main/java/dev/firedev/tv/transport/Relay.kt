package dev.firedev.tv.transport

import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * A listener on 127.0.0.1 inside the TV that carries each connection to the user's machine through [open]: a pinned
 * TLS socket on the LAN, or an SSH direct-tcpip channel. The TV interface only ever talks to 127.0.0.1, so it can't
 * tell (and doesn't care) which one it is, and the WebView treats the address as a secure context.
 */
class Relay(private val name: String, preferredPort: Int, private val open: () -> Upstream) : Closeable {
    interface Upstream : Closeable {
        val input: InputStream
        val output: OutputStream
    }

    private val server: ServerSocket = bind(preferredPort)
    private val pool: ExecutorService = Executors.newCachedThreadPool { r -> Thread(r, "relay-$name").apply { isDaemon = true } }

    @Volatile var lastError: String? = null
        private set

    /** Called with each failure to reach the machine (the UI shows "reconnecting"). */
    @Volatile var onError: ((Exception) -> Unit)? = null

    val port: Int get() = server.localPort
    val url: String get() = "http://127.0.0.1:$port"

    fun start(): Relay {
        pool.execute {
            while (!server.isClosed) {
                val client = try { server.accept() } catch (e: IOException) { break }
                pool.execute { handle(client) }
            }
        }
        return this
    }

    private fun handle(client: Socket) {
        client.tcpNoDelay = true
        val up = try {
            open().also { lastError = null }
        } catch (e: Exception) {
            lastError = e.message ?: e.javaClass.simpleName
            onError?.invoke(e)
            runCatching { client.close() }
            return
        }
        val toMachine = pool.submit { pump(client.getInputStream(), up.output) { runCatching { up.close() } } }
        pump(up.input, client.getOutputStream()) { runCatching { client.close() } }
        runCatching { client.close() }
        runCatching { up.close() }
        toMachine.cancel(true)
    }

    private inline fun pump(from: InputStream, to: OutputStream, done: () -> Unit) {
        val buf = ByteArray(32 * 1024)
        try {
            while (true) {
                val n = from.read(buf)
                if (n < 0) break
                to.write(buf, 0, n)
                to.flush()
            }
        } catch (_: IOException) {
        } finally {
            done()
        }
    }

    override fun close() {
        runCatching { server.close() }
        pool.shutdownNow()
    }

    companion object {
        /** The same port across app starts when it's free, so the interface's addresses stay the same. */
        private fun bind(preferred: Int): ServerSocket {
            val lo = InetAddress.getByName("127.0.0.1")
            if (preferred > 0) {
                try { return ServerSocket(preferred, 64, lo).apply { reuseAddress = true } } catch (_: IOException) {}
            }
            return ServerSocket(0, 64, lo)
        }

        fun of(socket: Socket): Upstream = object : Upstream {
            override val input: InputStream = socket.getInputStream()
            override val output: OutputStream = socket.getOutputStream()
            override fun close() = socket.close()
        }
    }
}

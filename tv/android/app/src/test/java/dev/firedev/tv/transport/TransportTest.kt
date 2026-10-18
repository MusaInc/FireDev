package dev.firedev.tv.transport

import okhttp3.WebSocket as OkWebSocket
import org.json.JSONObject
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.net.ServerSocket
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocketListener
import java.nio.file.Files
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * The TV's transports against the real thing: a FireDev service in LAN mode (TLS, pinned certificate, bound pairing
 * codes) and a private sshd (its own host key and authorized_keys, on a high port) in front of the same service.
 * Everything the TV interface would do goes to 127.0.0.1 relays, as in the app. FIREDEV_TEST_TRANSPORT=1 to run.
 */
class TransportTest {
    companion object {
        private lateinit var dir: File
        private var service: Process? = null
        private var sshd: Process? = null
        private val ports = IntArray(6) { freePort() }
        private val apiPort get() = ports[0]
        private val previewPort get() = ports[1]
        private val lanPort get() = ports[2]
        private val lanPreviewPort get() = ports[3]
        private val sshPort get() = ports[4]
        private val seed = TvKey.newSeed()
        private val key = TvKey.fromSeed(seed)

        private fun freePort(): Int = ServerSocket(0).use { it.localPort }
        private val repo get() = File(System.getenv("FIREDEV_REPO") ?: "../..")

        @BeforeClass @JvmStatic fun up() {
            assumeTrue("FIREDEV_TEST_TRANSPORT=1 to run", System.getenv("FIREDEV_TEST_TRANSPORT") == "1")
            dir = Files.createTempDirectory("firedev-transport").toFile()
            val home = File(dir, "home").apply { mkdirs() }
            val proj = File(dir, "web").apply { mkdirs() }
            File(proj, "index.html").writeText("<!doctype html><title>Through the TV</title><h1>hello</h1>")
            val conf = File(dir, "firedev.yaml")
            conf.writeText("""
                server: {host: 127.0.0.1, port: $apiPort, preview_port: $previewPort, lan: true, lan_port: $lanPort,
                         lan_preview_port: $lanPreviewPort, name: Test Laptop, data_dir: ${dir.absolutePath}/data}
                machines: {this-computer: {local: true}}
                projects: [{id: web, name: Web, machine: this-computer, path: ${proj.absolutePath}, agent: shell, tmux_session: firedev-transport-test-$apiPort}]
            """.trimIndent())
            service = ProcessBuilder(File(repo, ".venv/bin/python").path, "-m", "firedev", "--config", conf.path, "serve")
                .directory(File(repo, "backend")).redirectErrorStream(true).redirectOutput(File(dir, "service.log"))
                .apply { environment()["FIREDEV_HOME"] = home.path; environment()["PYTHONPATH"] = File(repo, "backend").path }.start()
            // A private sshd for the SSH mode: own host key, the TV key as the only authorized key, env pointing at the test service.
            val hostKey = File(dir, "host_ed25519")
            ProcessBuilder("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", hostKey.path).start().waitFor()
            File(dir, "authorized_keys").writeText(TvKey.publicLine(key, "firedev-tv-test") + "\n")
            val sshdConf = File(dir, "sshd_config")
            sshdConf.writeText("""
                Port $sshPort
                ListenAddress 127.0.0.1
                HostKey ${hostKey.path}
                AuthorizedKeysFile ${dir.absolutePath}/authorized_keys
                PasswordAuthentication no
                KbdInteractiveAuthentication no
                UsePAM no
                StrictModes no
                PidFile ${dir.absolutePath}/sshd.pid
                SetEnv FIREDEV_HOME=${home.path} FIREDEV_CONFIG=${conf.path}
            """.trimIndent())
            sshd = ProcessBuilder("/usr/sbin/sshd", "-D", "-e", "-f", sshdConf.path).redirectErrorStream(true)
                .redirectOutput(File(dir, "sshd.log")).start()
            waitFor(apiPort)
            waitFor(lanPort)
            waitFor(sshPort)
        }

        @AfterClass @JvmStatic fun down() {
            ProcessBuilder("tmux", "kill-session", "-t", "=firedev-transport-test-$apiPort").start().waitFor()
            service?.destroy()
            sshd?.destroy()
            service?.waitFor(5, TimeUnit.SECONDS)
        }

        private fun waitFor(port: Int) {
            repeat(100) {
                try { java.net.Socket("127.0.0.1", port).close(); return } catch (_: Exception) { Thread.sleep(200) }
            }
            fail("nothing listens on $port; logs in $dir")
        }

        class Res(val code: Int, val body: String, val headers: okhttp3.Headers)
        private val http = OkHttpClient.Builder().readTimeout(60, TimeUnit.SECONDS).build()
        private val noRedirects = http.newBuilder().followRedirects(false).build()
        private fun call(c: OkHttpClient, r: Request): Res = c.newCall(r).execute().use { Res(it.code, it.body!!.string(), it.headers) }
        fun get(url: String, token: String? = null, cookie: String? = null, follow: Boolean = true): Res = call(if (follow) http else noRedirects,
            Request.Builder().url(url).apply { if (token != null) header("Authorization", "Bearer $token"); if (cookie != null) header("Cookie", cookie) }.build())
        fun post(url: String, body: String, token: String): Res = call(http, Request.Builder().url(url).header("Authorization", "Bearer $token")
            .post(body.toRequestBody("application/json".toMediaType())).build())

        /** Messages from the TV interface's WebSocket, through the relay. */
        fun ws(url: String): Pair<OkWebSocket, LinkedBlockingQueue<String>> {
            val q = LinkedBlockingQueue<String>()
            val sock = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
                override fun onMessage(webSocket: OkWebSocket, text: String) { q.add(text) }
            })
            return sock to q
        }

        val adminToken: String get() = File(dir, "home/admin.token").readText().trim()
    }

    @Test fun lanRelayPinsTheCertificateAndPairsWithABoundCode() {
        val t = Transport(Profile("lan", "Test Laptop", "127.0.0.1", lanPort, previewPort = lanPreviewPort), null, 0, 0).start()
        try {
            // 127.0.0.1 inside the "TV" really reaches the computer, over TLS.
            val info = JSONObject(get(t.server + "/api/info").body)
            assertEquals("Test Laptop", info.getString("name"))
            assertEquals(info.getString("fingerprint"), t.tls!!.seen)
            val claim = "a".repeat(32)
            val (sock, q) = ws(t.server.replace("http", "ws") + "/ws?pair=$claim")
            val pairing = JSONObject(q.poll(10, TimeUnit.SECONDS))
            assertTrue(pairing.getBoolean("bound"))
            val code = PinnedTls.pairCode(t.tls.seen!!, claim, pairing.getString("nonce"))
            assertEquals("the TV computes the code the computer expects", pairing.getString("code"), code)
            assertEquals(200, post("http://127.0.0.1:$apiPort/api/pair", """{"code":"$code","name":"Living room"}""", adminToken).code)
            var paired: JSONObject? = null
            while (paired == null) {
                val m = JSONObject(q.poll(10, TimeUnit.SECONDS) ?: fail("no paired message").toString())
                if (m.getString("type") == "paired") paired = m
            }
            sock.cancel()
            val token = paired.getString("token")
            assertEquals(200, get(t.server + "/api/projects", token).code)
            // The preview gateway through its own relay (its own origin), refusing without a ticket.
            assertEquals(403, get(t.previewBase + "/").code)
        } finally { t.close() }
    }

    @Test fun lanRelayRefusesAChangedCertificate() {
        val t = Transport(Profile("lan", "x", "127.0.0.1", lanPort, previewPort = lanPreviewPort, fp = "00".repeat(32)), null, 0, 0).start()
        try {
            try { get(t.server + "/api/info"); fail("a different certificate must not connect") } catch (_: java.io.IOException) {}
            assertTrue(t.api.lastError!!.contains("certificate changed"))
        } finally { t.close() }
    }

    @Test fun sshFromTheTvWithHostKeyCheckBootstrapAndForwardedPreview() {
        val user = System.getProperty("user.name")
        // First contact: the host key is unknown and must be confirmed against what the server says it is.
        val probe = SshLink("127.0.0.1", sshPort, user, key, null)
        val unknown = try { probe.ensure(); null } catch (e: UnknownHostKey) { e }
        assertNotNull(unknown)
        val keygen = ProcessBuilder("ssh-keygen", "-lf", File(dir, "host_ed25519.pub").path).start().inputStream.bufferedReader().readText()
        assertTrue("the TV shows the same fingerprint as ssh-keygen -lf: $keygen vs ${unknown!!.fingerprint}", keygen.contains(unknown.fingerprint))
        // Confirmed: log in with the TV's key, start/find FireDev, get a TV token (over SSH, no pairing code).
        probe.hostKeyPin = unknown.fingerprint
        val server = SshSetup.bootstrap(probe, "Sony TV (test)")
        probe.close()
        assertEquals(apiPort, server.apiPort)
        val t = Transport(Profile("ssh", server.name, "127.0.0.1", sshPort, user = user, hostKey = unknown.fingerprint,
            apiPort = server.apiPort, remotePreviewPort = server.previewPort, token = server.token), key, 0, 0).start()
        try {
            assertEquals(200, get(t.server + "/api/projects", server.token).code)
            // A real preview: started through the API, then loaded through the forwarded gateway with a ticket.
            val open = JSONObject(post(t.server + "/api/command",
                """{"name":"preview_open","args":{"project":"web","start":true,"preview_base":"${t.previewBase}"}}""", server.token).body)
            assertTrue(open.toString(), open.getBoolean("ok"))
            var url: String? = null
            repeat(40) {
                if (url == null) {
                    val r = JSONObject(post(t.server + "/api/command",
                        """{"name":"preview_open","args":{"project":"web","preview_base":"${t.previewBase}"}}""", server.token).body)
                    url = if (r.isNull("url")) null else r.getString("url")
                    if (url == null) Thread.sleep(500)
                }
            }
            assertNotNull("the preview starts", url)
            assertTrue(url!!.startsWith(t.previewBase))
            val enter = get(url!!, follow = false)
            assertEquals(303, enter.code)
            val cookie = enter.headers["set-cookie"]!!.substringBefore(";")
            val page = get(t.previewBase + "/", cookie = cookie, follow = false)
            assertTrue(page.body.contains("Through the TV") && page.body.contains("/__firedev/tv.js"))
            // The network drops: the next request reconnects SSH by itself.
            t.ssh!!.close()
            assertEquals(200, get(t.server + "/api/projects", server.token).code)
        } finally { t.close() }
    }

    @Test fun sshRefusesAChangedHostKey() {
        val link = SshLink("127.0.0.1", sshPort, System.getProperty("user.name"), key, "SHA256:notthisone")
        try { link.ensure(); fail("must refuse") } catch (e: HostKeyMismatch) { assertTrue(e.message!!.contains("changed")) }
    }

    @Test fun aStandardSshdShowsItsEd25519Fingerprint() {
        // What the TV tells users to compare with: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
        val pub = File("/etc/ssh/ssh_host_ed25519_key.pub")
        assumeTrue(pub.exists())
        val shown = try { SshLink("127.0.0.1", 22, "nobody", key, null).ensure(); null } catch (e: UnknownHostKey) { e.fingerprint }
        val keygen = ProcessBuilder("ssh-keygen", "-lf", pub.path).start().inputStream.bufferedReader().readText()
        assertTrue("TV shows $shown, ssh-keygen says $keygen", shown != null && keygen.contains(shown))
    }

    @Test fun pairCodeMatchesTheComputersDerivation() {
        // Same inputs as backend/tests: auth.bound_code("ab"*32, "c"*32, "00ff")
        val py = ProcessBuilder(File(repo, ".venv/bin/python").path, "-c",
            "import sys; sys.path.insert(0,'backend'); from firedev.auth import bound_code; print(bound_code('ab'*32,'c'*32,'00ff'))")
            .directory(repo).start().inputStream.bufferedReader().readText().trim()
        assertEquals(py, PinnedTls.pairCode("ab".repeat(32), "c".repeat(32), "00ff"))
    }
}

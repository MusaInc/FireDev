package dev.firedev.tv.transport

import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Not a unit test: runs the TV app's real transport (the same classes the APK uses) so a browser can stand in for the
 * WebView and drive the TV interface through it. Start with FIREDEV_SIM=<dir> and a profile in <dir>/sim.json:
 *   {"mode": "ssh", "host", "port", "user", "seed": hex}  -> SSH first contact (host key checked against <dir>/host.pub),
 *                                                          bootstrap (`firedev up`, TV token), relays
 *   {"mode": "lan", "host", "port", "previewPort"}       -> pinned-TLS relays (pairing happens in the interface)
 * Writes <dir>/relay.json ({server, previewBase, token, fp}) and keeps running while <dir>/running exists.
 */
class ShellSim {
    @Test fun run() {
        val dir = System.getenv("FIREDEV_SIM")
        assumeTrue("FIREDEV_SIM=<dir> to run", dir != null)
        val d = File(dir!!)
        val j = JSONObject(File(d, "sim.json").readText())
        val out = JSONObject()
        val t: Transport = if (j.getString("mode") == "ssh") {
            val key = TvKey.fromSeed(j.getString("seed").chunked(2).map { it.toInt(16).toByte() }.toByteArray())
            File(d, "tv.pub").writeText(TvKey.publicLine(key, "firedev-tv-sim") + "\n")
            val link = SshLink(j.getString("host"), j.getInt("port"), j.getString("user"), key, null)
            val fp = try { link.ensure(); error("host key was not checked") } catch (e: UnknownHostKey) { e.fingerprint }
            val expected = ProcessBuilder("ssh-keygen", "-lf", File(d, "host.pub").path).start().inputStream.bufferedReader().readText()
            check(expected.contains(fp)) { "fingerprint $fp isn't the server's ($expected)" }
            link.hostKeyPin = fp
            val server = SshSetup.bootstrap(link, "Sony TV (sim)")
            link.close()
            out.put("token", server.token).put("hostKey", fp).put("name", server.name)
            Transport(Profile("ssh", server.name, j.getString("host"), j.getInt("port"), user = j.getString("user"), hostKey = fp,
                apiPort = server.apiPort, remotePreviewPort = server.previewPort, token = server.token), key, 0, 0).start()
        } else {
            Transport(Profile("lan", j.optString("name", "Computer"), j.getString("host"), j.getInt("port"), previewPort = j.getInt("previewPort")), null, 0, 0).start()
        }
        out.put("server", t.server).put("previewBase", t.previewBase)
        // LAN: the certificate is seen on the first relayed connection; report it once it is.
        val running = File(d, "running")
        val relay = File(d, "relay.json")
        relay.writeText(out.toString())
        val deadline = System.currentTimeMillis() + 60 * 60 * 1000
        while (running.exists() && System.currentTimeMillis() < deadline) {
            val seen = t.tls?.seen
            if (seen != null && out.optString("fp") != seen) { out.put("fp", seen); relay.writeText(out.toString()) }
            Thread.sleep(200)
        }
        t.close()
    }
}

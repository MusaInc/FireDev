package dev.firedev.tv

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Finds FireDev computers on the same Wi-Fi: `firedev serve` announces `_firedev._tcp` with mDNS (its name, its
 * certificate's fingerprint and its ports). Resolves one service at a time, as older Android versions require.
 */
class Discovery(context: Context, private val onChange: (JSONArray) -> Unit) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager?
    private val main = Handler(Looper.getMainLooper())
    private var lock: WifiManager.MulticastLock? = null
    private var listener: NsdManager.DiscoveryListener? = null
    private val found = LinkedHashMap<String, JSONObject>()
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false

    fun start() {
        if (listener != null) return
        lock = wifi?.createMulticastLock("firedev")?.apply { setReferenceCounted(false); runCatching { acquire() } }
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { Log.w(TAG, "discovery failed $errorCode"); listener = null }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceFound(info: NsdServiceInfo) = main.post { queue.add(info); next() }.let { }
            override fun onServiceLost(info: NsdServiceInfo) = main.post {
                if (found.remove(info.serviceName) != null) publish()
            }.let { }
        }
        listener = l
        runCatching { nsd.discoverServices(TYPE, NsdManager.PROTOCOL_DNS_SD, l) }.onFailure { listener = null }
    }

    fun stop() {
        listener?.let { runCatching { nsd.stopServiceDiscovery(it) } }
        listener = null
        runCatching { lock?.release() }
        queue.clear()
    }

    private fun next() {
        if (resolving) return
        val info = queue.removeFirstOrNull() ?: return
        resolving = true
        @Suppress("DEPRECATION")
        nsd.resolveService(info, object : NsdManager.ResolveListener {
            override fun onResolveFailed(si: NsdServiceInfo, errorCode: Int) = main.post { resolving = false; next() }.let { }
            override fun onServiceResolved(si: NsdServiceInfo) = main.post {
                resolving = false
                val attrs = si.attributes.mapValues { (_, v) -> v?.let { String(it) } ?: "" }
                @Suppress("DEPRECATION")
                val host = si.host?.hostAddress
                if (host != null && attrs["fp"]?.length == 64) {
                    found[si.serviceName] = JSONObject().put("key", si.serviceName).put("name", attrs["name"] ?: si.serviceName)
                        .put("id", attrs["id"] ?: si.serviceName).put("host", host).put("port", si.port)
                        .put("previewPort", attrs["pp"]?.toIntOrNull() ?: 0).put("fp", attrs["fp"])
                    publish()
                }
                next()
            }.let { }
        })
    }

    private fun publish() = onChange(JSONArray(found.values.toList()))

    companion object {
        private const val TYPE = "_firedev._tcp."
        private const val TAG = "FireDevDiscovery"
    }
}

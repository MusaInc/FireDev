package dev.firedev.tv

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.json.JSONObject
import java.security.Security

/**
 * FireDev on Fire TV: a full-screen WebView running the TV interface from assets, plus what only the device can
 * do: remote keys, lifecycle, the saved server address, and on-device speech recognition where it exists.
 * All development work happens on the user's own machines through the FireDev service.
 */
class MainActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var settings: ServerSettings
    private var speech: SpeechInput? = null
    private lateinit var connection: Connection
    private var discovery: Discovery? = null
    private val main = Handler(Looper.getMainLooper())
    private val queued = mutableListOf<String>()
    private var pageReady = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY

        settings = ServerSettings(getSharedPreferences("firedev", MODE_PRIVATE), BuildConfig.FIREDEV_SERVER, BuildConfig.FIREDEV_TOKEN)
        settings.applyIntent(intent)
        // sshj needs the full BouncyCastle, not Android's trimmed copy registered under the same name.
        Security.removeProvider("BC")
        Security.insertProviderAt(BouncyCastleProvider(), 1)
        // The user's own machine: a computer on this Wi-Fi or their SSH server, reached through 127.0.0.1 relays.
        connection = Connection(getSharedPreferences("firedev", MODE_PRIVATE), ::emit)
        connection.start()
        speech = SpeechInput.create(this, ::emit)

        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#09090b"))
            isFocusable = true
            isFocusableInTouchMode = true
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.setSupportZoom(false)
            settings.builtInZoomControls = false
            settings.displayZoomControls = false
            // The page scales its own 1920×1080 stage to the screen: keep the viewport at the real screen size and
            // ignore the system font scale, which would otherwise enlarge text past the fixed layout.
            settings.useWideViewPort = false
            settings.loadWithOverviewMode = false
            settings.textZoom = 100
            addJavascriptInterface(Bridge(), "FireDevNative")
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView, url: String) {
                    pageReady = true
                    queued.forEach { view.evaluateJavascript(it, null) }
                    queued.clear()
                }
                // The interface never navigates away from its own files. Only the live-preview frame inside it loads
                // pages from elsewhere (the preview gateway), and it is sandboxed against navigating the interface.
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) =
                    request.isForMainFrame && !request.url.toString().startsWith("file:///android_asset/")
            }
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                    if (m.messageLevel() == ConsoleMessage.MessageLevel.ERROR) Log.e(TAG, "${m.message()} @${m.sourceId()}:${m.lineNumber()}")
                    return true
                }
            }
        }
        // The live preview's frame (on the preview gateway) keeps its session in a cookie; next to a local-file
        // interface that cookie counts as third-party, which WebView blocks unless asked.
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(webView, true)
        }
        setContentView(webView)
        webView.requestFocus()
        webView.loadUrl("file:///android_asset/index.html")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (settings.applyIntent(intent)) webView.reload()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
        emit(JSONObject().put("type", "lifecycle").put("state", "resume"))
    }

    override fun onPause() {
        emit(JSONObject().put("type", "lifecycle").put("state", "pause"))
        speech?.stop()
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        discovery?.stop()
        connection.transport?.close()
        speech?.destroy()
        webView.destroy()
        super.onDestroy()
    }

    /**
     * Remote keys. BACK becomes Escape so the interface decides what Back means; the centre button is Enter (with
     * key-up, for the held confirmation on destructive approvals). Menu, Play/Pause, Rewind and Fast-forward go
     * straight to the interface's key hook because WebViews deliver them unreliably.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val hook = RemoteKeys.hookAction(event.keyCode)
        if (hook != null) {
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) sendKey(hook, true)
            if (event.action == KeyEvent.ACTION_UP) sendKey(hook, false)
            return true
        }
        val mapped = RemoteKeys.mappedKeyCode(event.keyCode)
        if (mapped != null) {
            webView.dispatchKeyEvent(KeyEvent(event.downTime, event.eventTime, event.action, mapped, event.repeatCount, event.metaState))
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    private fun sendKey(action: String, down: Boolean) {
        if (pageReady) webView.evaluateJavascript("window.__fdKey && window.__fdKey('$action', $down);", null)
    }

    /** Sends a native event to the page, queueing it until the page has loaded. */
    private fun emit(event: JSONObject) {
        val js = "window.__fdNative && window.__fdNative($event);"
        main.post { if (pageReady) webView.evaluateJavascript(js, null) else queued += js }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == MIC_REQUEST) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) speech?.start()
            else emit(JSONObject().put("type", "speech").put("phase", "error").put("text", "Microphone permission denied"))
        }
    }

    /** The JavaScript interface used by tv/web/src/native.js. */
    inner class Bridge {
        @JavascriptInterface fun getConfig(): String {
            val cfg = settings.toJson(BuildConfig.VERSION_NAME)
            val c = connection.config()
            if (c != null) {
                for (k in c.keys()) cfg.put(k, c.get(k))
            } else if (settings.isDefaultServer && BuildConfig.FIREDEV_SERVER.isBlank()) {
                cfg.put("server", "") // nothing chosen yet: the interface starts the setup
            }
            return cfg.put("device", connection.tvName).put("transports", true).toString()
        }
        @JavascriptInterface fun startDiscovery() = main.post {
            if (discovery == null) discovery = Discovery(this@MainActivity) { list -> emit(JSONObject().put("type", "discovery").put("machines", list)) }
            discovery?.start()
        }.let { }
        @JavascriptInterface fun stopDiscovery() = main.post { discovery?.stop() }.let { }
        @JavascriptInterface fun connectLan(json: String) = connection.connectLan(JSONObject(json))
        @JavascriptInterface fun connectSsh(json: String) = connection.connectSsh(JSONObject(json))
        @JavascriptInterface fun trustHostKey(fingerprint: String) = connection.trustHostKey(fingerprint)
        @JavascriptInterface fun publicKey(): String = connection.publicKey()
        @JavascriptInterface fun publicKeyFingerprint(): String = connection.keyFingerprint()
        @JavascriptInterface fun pairCode(claim: String, nonce: String): String? = connection.pairCode(claim, nonce)
        @JavascriptInterface fun paired() = connection.paired()
        @JavascriptInterface fun forgetConnection() = connection.forget()
        @JavascriptInterface fun speechAvailable(): Boolean = speech != null
        @JavascriptInterface fun startSpeech() = main.post {
            if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MIC_REQUEST)
            } else {
                speech?.start()
            }
        }.let { }
        @JavascriptInterface fun stopSpeech() = main.post { speech?.stop() }.let { }
        @JavascriptInterface fun attention() = main.post {
            runCatching { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 40).startTone(ToneGenerator.TONE_PROP_ACK, 160) }
        }.let { }
        @JavascriptInterface fun exitApp() = main.post { finish() }.let { }
    }

    companion object {
        private const val TAG = "FireDev"
        private const val MIC_REQUEST = 7
    }
}

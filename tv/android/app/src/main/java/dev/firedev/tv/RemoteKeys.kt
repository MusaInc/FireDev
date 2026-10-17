package dev.firedev.tv

import android.view.KeyEvent

/** How Fire TV remote keys reach the interface (pure, so it can be unit-tested). */
object RemoteKeys {
    /** Keys re-sent to the WebView as keyboard keys the interface understands. */
    fun mappedKeyCode(keyCode: Int): Int? = when (keyCode) {
        KeyEvent.KEYCODE_BACK -> KeyEvent.KEYCODE_ESCAPE
        KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_BUTTON_A -> KeyEvent.KEYCODE_ENTER
        else -> null
    }

    /** Keys delivered straight to window.__fdKey(action, down). */
    fun hookAction(keyCode: Int): String? = when (keyCode) {
        KeyEvent.KEYCODE_MENU -> "menu"
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE -> "playpause"
        KeyEvent.KEYCODE_MEDIA_REWIND -> "rewind"
        KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> "forward"
        else -> null
    }
}

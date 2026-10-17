package dev.firedev.tv

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RemoteKeysTest {
    @Test fun backBecomesEscape() = assertEquals(KeyEvent.KEYCODE_ESCAPE, RemoteKeys.mappedKeyCode(KeyEvent.KEYCODE_BACK))
    @Test fun centreBecomesEnter() = assertEquals(KeyEvent.KEYCODE_ENTER, RemoteKeys.mappedKeyCode(KeyEvent.KEYCODE_DPAD_CENTER))
    @Test fun arrowsPassThrough() = assertNull(RemoteKeys.mappedKeyCode(KeyEvent.KEYCODE_DPAD_LEFT))
    @Test fun menuOpensVoice() = assertEquals("menu", RemoteKeys.hookAction(KeyEvent.KEYCODE_MENU))
    @Test fun playPauseIsHooked() = assertEquals("playpause", RemoteKeys.hookAction(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE))
    @Test fun mediaSeekKeysAreHooked() {
        assertEquals("rewind", RemoteKeys.hookAction(KeyEvent.KEYCODE_MEDIA_REWIND))
        assertEquals("forward", RemoteKeys.hookAction(KeyEvent.KEYCODE_MEDIA_FAST_FORWARD))
    }

    @Test fun serverUrlValidation() {
        assertTrue(ServerSettings.isValidServer("http://192.168.1.20:8130"))
        assertTrue(ServerSettings.isValidServer("https://firedev.example.com"))
        assertFalse(ServerSettings.isValidServer("javascript:alert(1)"))
        assertFalse(ServerSettings.isValidServer("file:///sdcard/x"))
    }
}

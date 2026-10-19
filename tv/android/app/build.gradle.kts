import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/**
 * Where the TV finds the FireDev service. Set firedev.server (and optionally firedev.token, a TV token from
 * `firedev add-assistant "Living room" --role tv`) in tv/android/local.properties or as -P properties.
 * Without a token the TV shows a pairing code instead. Both can be changed later without rebuilding:
 *   adb shell am start -n dev.firedev.tv/.MainActivity -e server http://192.168.1.20:8130
 */
val local = Properties().apply { rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) } }
fun setting(name: String, default: String) = (project.findProperty(name) as String?) ?: local.getProperty(name) ?: default

android {
    namespace = "dev.firedev.tv"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.firedev.tv"
        minSdk = 22
        targetSdk = 34
        versionCode = 11
        versionName = "0.3.6"
        buildConfigField("String", "FIREDEV_SERVER", "\"${setting("firedev.server", "")}\"")
        buildConfigField("String", "FIREDEV_TOKEN", "\"${setting("firedev.token", "")}\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Hackathon builds are signed with the debug key so they sideload onto a Fire TV in developer mode.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions.jvmTarget = "17"
    buildFeatures.buildConfig = true
    testOptions.unitTests.isReturnDefaultValues = true

    packaging.resources.excludes += setOf("META-INF/versions/9/OSGI-INF/MANIFEST.MF", "META-INF/DEPENDENCIES", "META-INF/LICENSE*", "META-INF/NOTICE*")
    // Transport tests reach a real sshd and a real FireDev service (FIREDEV_TEST_TRANSPORT=1).
    testOptions.unitTests.all { it.environment("FIREDEV_REPO", rootProject.file("../..").absolutePath) }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/firedev-web"))
}

// The TV interface is the same web build the browser preview uses: copy it in, never into the source tree.
val copyWeb by tasks.registering(Sync::class) {
    from(rootProject.file("../web")) {
        include("index.html", "styles.css", "dist/**", "fonts/**")
    }
    into(layout.buildDirectory.dir("generated/firedev-web"))
}
tasks.named("preBuild") { dependsOn(copyWeb) }

dependencies {
    // SSH from the TV to the user's own server (remote mode); BouncyCastle for its key exchange on Android.
    implementation("com.hierynomus:sshj:0.39.0")
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
    implementation("net.i2p.crypto:eddsa:0.3.0") // the TV key (Ed25519), the type sshj signs with
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("com.squareup.okhttp3:okhttp:4.12.0")
}

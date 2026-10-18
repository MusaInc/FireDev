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
        versionCode = 3
        versionName = "0.1.2"
        buildConfigField("String", "FIREDEV_SERVER", "\"${setting("firedev.server", "http://10.0.2.2:8130")}\"")
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
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

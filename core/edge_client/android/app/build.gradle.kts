plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}
android {
    namespace = "com.example.nexus_edge"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.nexus_edge"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName

        // Restrict to arm64-v8a only for now, matching the SM A566B test
        // device and every current-generation Android phone -- avoids
        // building/bundling armeabi-v7a/x86/x86_64 variants of the native
        // libs that are never actually exercised. Widen this later if
        // older 32-bit devices ever need support.
        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                // Passed straight through to cv_engine's CMakeLists.txt.
                // OpenCV_DIR points at the SDK's static-libs config
                // (not the default native/jni, which resolves to the
                // fat shared libopencv_java4.so) so ai_corrector.so gets
                // OpenCV baked in directly instead of depending on a
                // 23MB shared library at runtime — see CMakeLists.txt's
                // OpenCV section for why. VERIFY sdk/native/staticlibs/
                // arm64-v8a/ actually exists in your installed SDK
                // version before relying on this; if it doesn't, drop
                // "/staticlibs" back to "/native/jni" and keep
                // libopencv_java4.so in jniLibs.srcDirs below.
                arguments(
                          "-DOpenCV_DIR=/home/eyad-amr/Desktop/OpenCV-android-sdk/sdk/native/jni",
                          "-DBUILD_SHARED_LIBS=OFF",
                          "-DCMAKE_BUILD_TYPE=Release",
                          "-DONNXRUNTIME_ROOT=/home/eyad-amr/Desktop/onnxruntime-android-1.28.0"
                         )
            }
        }
    }

    externalNativeBuild {
        cmake {
            // Points at the actual cv_engine CMakeLists.txt -- built as
            // part of THIS app's native build rather than requiring a
            // separately pre-built .so, so Gradle handles recompiling it
            // automatically whenever the C++ source changes.
            path = file("../../../cv_engine/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
            // Shrinks/obfuscates Kotlin/Java code and drops unused
            // resources in the release build — wasn't previously
            // enabled, so the debug-shaped release APK was carrying
            // dead code and resources on top of the native-lib bloat.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    sourceSets {
        getByName("main") {
            // ONNX Runtime's prebuilt .so still ships as a real runtime
            // shared-library dependency — that one's legitimate and
            // still needs to be here. libopencv_java4.so (previously
            // bundled from .../sdk/native/libs, the SDK's *shared*-lib
            // folder) is dropped: with OpenCV linked statically into
            // ai_corrector.so instead (see the externalNativeBuild
            // arguments above), nothing in the APK depends on it at
            // load time anymore, and it was ~23MB of dead weight sitting
            // in every build.
            jniLibs.srcDirs(
                "C:/Users/asus/Downloads/onnxruntime-android-1.28.0/jni"
            )
        }
    }
}
kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}
flutter {
    source = "../.."
}
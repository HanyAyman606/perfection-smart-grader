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
                // Passed straight through to the NEW cv_engine's
                // CMakeLists.txt (7-target pipeline: stage1_boxes …
                // stage6_scoring, run_pipeline, exam_scanner_ffi). This
                // previously pointed at settings for the OLD single-lib
                // ai_corrector engine (static OpenCV, no ONNXRUNTIME_ROOT
                // ANDROID branch existed yet) — update BOTH paths below to
                // match your actual installed Android OpenCV SDK / ONNX
                // Runtime Android package locations; these are placeholders.
                // OpenCV_DIR: point at the SDK's config, NOT staticlibs —
                // the new engine's 6 stage executables + exam_scanner_ffi.so
                // all link OpenCV independently, so a static link would
                // duplicate ~20MB+ of OpenCV code across 7 binaries. Prefer
                // the SDK's shared-lib jni/ config and keep
                // libopencv_java4.so in jniLibs.srcDirs below (already
                // there).
                arguments(
                          "-DOpenCV_DIR=/home/eyad-amr/Desktop/OpenCV-android-sdk/sdk/native/jni",
                          "-DCMAKE_BUILD_TYPE=Release",
                          "-DONNXRUNTIME_ROOT=/home/eyad-amr/Desktop/onnxruntime-android-1.28.0"
                         )
            }
        }
    }

    externalNativeBuild {
        cmake {
            // Points at the NEW cv_engine's CMakeLists.txt -- built as
            // part of THIS app's native build rather than requiring a
            // separately pre-built .so, so Gradle handles recompiling it
            // automatically whenever the C++ source changes.
            //
            // NOTE: the CMakeLists.txt's ANDROID block repackages the 6
            // stage executables as lib<stage>.so so Gradle's packaging
            // step includes them (plain add_executable() outputs are
            // otherwise silently dropped from the APK) — this is
            // UNVERIFIED on a real device, see that file's comment.
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
            jniLibs.srcDirs(
                "/home/eyad-amr/Desktop/onnxruntime-android-1.28.0/jni"
            )
        }
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
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
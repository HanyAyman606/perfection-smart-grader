# Keep FFI/JNI entry points reachable from native code — R8 can't see
# calls made purely from the C++ side via dart:ffi's dynamic symbol
# lookup, so anything the native engine calls back into Java/Kotlin for
# (currently nothing beyond standard Flutter plugin glue) must stay
# un-obfuscated and un-stripped here if that ever changes.

# Flutter's own plugin registrant machinery.
-keep class io.flutter.plugins.** { *; }
-keep class io.flutter.embedding.** { *; }

# Flutter's engine has an optional code path for Play Store "deferred
# components" (dynamic feature module install via the Play Core split-
# install API). This app doesn't use deferred components and doesn't
# depend on com.google.android.play:core, so those classes genuinely
# don't exist in the build — R8 fails hard on that by default even
# though the code path is never reached at runtime. Telling it not to
# warn about these specific classes (rather than -keep, since they don't
# exist to keep) is Flutter's own documented fix for this, not something
# specific to this project's native/OpenCV changes.
-dontwarn com.google.android.play.core.splitcompat.**
-dontwarn com.google.android.play.core.splitinstall.**
-dontwarn com.google.android.play.core.tasks.**
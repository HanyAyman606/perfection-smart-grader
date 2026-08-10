#pragma once

// extern "C" alone controls name mangling, not export visibility.
// On Windows/MSVC, a function must also be explicitly marked
// __declspec(dllexport) (when building the DLL) to actually be exported
// -- without it, MSVC produces a DLL with zero exported symbols: no
// import .lib gets generated, and Dart's DynamicLibrary.open() +
// lookupFunction() would fail at runtime even if the DLL itself loads,
// since there'd be nothing in it to find by name.
//
// GCC/Clang (Linux, Android) export all public symbols from a shared
// library by default, which is why this was never caught there --
// AI_CORRECTOR_EXPORT expands to nothing on those platforms, matching
// the previous behavior exactly.
#if defined(_WIN32)
    #if defined(AI_CORRECTOR_BUILDING_DLL)
        #define AI_CORRECTOR_EXPORT __declspec(dllexport)
    #else
        #define AI_CORRECTOR_EXPORT __declspec(dllimport)
    #endif
#else
    #define AI_CORRECTOR_EXPORT
#endif

extern "C" {
    AI_CORRECTOR_EXPORT const char* process_exam_in_memory(const char* image_path, const char* config_json);
    AI_CORRECTOR_EXPORT void free_string(char* str);
}

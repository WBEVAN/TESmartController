/* TESmart Kit.app main executable.

   Stays the process for the life of the kit and loads the Python named in
   Contents/Resources/python-host. macOS then attributes LAN connections to
   com.tesmartctl.kit. Replacing this process with exec drops that bundle id.

   Rebuild the shipped universal binary from this directory:

     clang -arch arm64 -mmacosx-version-min=11.0 -Os -o /tmp/tesmart-kit-arm64 kit_stub.c
     clang -arch x86_64 -mmacosx-version-min=11.0 -Os -o /tmp/tesmart-kit-x64 kit_stub.c
     lipo -create -output tesmart-kit /tmp/tesmart-kit-arm64 /tmp/tesmart-kit-x64

   The deployment target has to stay 11.0. Clang otherwise stamps the build
   machine's SDK (this stub was once 27.0), and Launch Services then refuses
   to open the app on older macOS (error -10825).
*/

#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int read_line(FILE *fp, char *buf, size_t cap) {
    if (fgets(buf, (int)cap, fp) == NULL) {
        return -1;
    }
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) {
        buf[--n] = '\0';
    }
    return n == 0 ? -1 : 0;
}

int main(int argc, char **argv) {
    char exe[8192];
    uint32_t size = (uint32_t)sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0) {
        fprintf(stderr, "tesmart-kit: executable path is too long\n");
        return 1;
    }
    char *slash = strrchr(exe, '/');
    if (slash == NULL) {
        fprintf(stderr, "tesmart-kit: executable path has no directory\n");
        return 1;
    }
    *slash = '\0';

    char cfg[8192];
    int wrote = snprintf(cfg, sizeof cfg, "%s/../Resources/python-host", exe);
    if (wrote < 0 || (size_t)wrote >= sizeof cfg) {
        fprintf(stderr, "tesmart-kit: python-host path is too long\n");
        return 1;
    }

    FILE *fp = fopen(cfg, "r");
    if (fp == NULL) {
        fprintf(stderr, "tesmart-kit: cannot read %s\n", cfg);
        return 1;
    }
    char libpython[8192];
    char python[8192];
    if (read_line(fp, libpython, sizeof libpython) != 0 || read_line(fp, python, sizeof python) != 0) {
        fclose(fp);
        fprintf(stderr, "tesmart-kit: %s must name libpython and the Python executable\n", cfg);
        return 1;
    }
    fclose(fp);

    setenv("PYTHONUNBUFFERED", "1", 1);
    void *lib = dlopen(libpython, RTLD_NOW | RTLD_GLOBAL);
    if (lib == NULL) {
        fprintf(stderr, "tesmart-kit: cannot load %s: %s\n", libpython, dlerror());
        return 1;
    }
    int (*Py_BytesMain)(int, char **) = dlsym(lib, "Py_BytesMain");
    if (Py_BytesMain == NULL) {
        fprintf(stderr, "tesmart-kit: %s has no Py_BytesMain\n", libpython);
        return 1;
    }

    int count = argc + 2;
    char **args = calloc((size_t)count + 1, sizeof(char *));
    if (args == NULL) {
        fprintf(stderr, "tesmart-kit: out of memory\n");
        return 1;
    }
    args[0] = python;
    args[1] = "-m";
    args[2] = "tesmart.kit_window";
    for (int i = 1; i < argc; i++) {
        args[i + 2] = argv[i];
    }
    return Py_BytesMain(count, args);
}

#define _POSIX_C_SOURCE 200809L
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ==========================================
 * Function: Open the portable Linux application directly from a file-manager click.
 * Method: Locate this ELF through procfs and run the bundled bootstrap with system Python in isolation.
 * ========================================== */
int main(int argc, char **argv) {
    char path[PATH_MAX], script[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (size < 0 || (size_t)size >= sizeof(path) - 1) return 1;
    path[size] = '\0';
    char *separator = strrchr(path, '/');
    if (!separator) return 1;
    *separator = '\0';
    int written = snprintf(script, sizeof(script), "%s/scripts/launch.py", path);
    if (written < 0 || (size_t)written >= sizeof(script) || access(script, R_OK)) {
        fprintf(stderr, "AutoReserve: keep this executable with its application folder.\n");
        return 1;
    }
    const char *code = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')";
    char **arguments = calloc((size_t)argc + 9, sizeof(char *));
    if (!arguments) return 1;
    arguments[0] = "/usr/bin/python3";
    arguments[1] = "-I";
    arguments[2] = "-c";
    arguments[3] = (char *)code;
    arguments[4] = path;
    arguments[5] = script;
    arguments[6] = "-m";
    arguments[7] = "autoreserve";
    for (int index = 1; index < argc; index++) arguments[index + 7] = argv[index];
    arguments[argc + 7] = NULL;
    if (argc == 1) setenv("AUTORESERVE_DESKTOP_LAUNCH", "1", 1);
    execv(arguments[0], arguments);
    perror("AutoReserve: cannot start the system Python 3 bootstrap");
    free(arguments);
    return 1;
}

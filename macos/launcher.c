/* desktop-sense launcher for macOS.
 *
 * macOS grants Screen Recording to the "responsible" app of a process, and a plain Python binary can't
 * even be added to that list in System Settings. So launchd starts this small program from
 * ~/Applications/desktop-sense.app (built by install.sh), and it starts the Python daemon as its child:
 * the permission prompt and the System Settings entry then say "desktop-sense".
 *
 * Because whatever this app starts inherits that permission, it only ever starts this installation's
 * daemon (DS_PYTHON DS_SCRIPT daemon, compiled in by install.sh), ignores its own arguments, and drops
 * DYLD_* and PYTHON* environment variables, which could otherwise load someone else's code into Python.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "ds_paths.h"  /* DS_PYTHON, DS_SCRIPT: written by install.sh for this installation */

extern char **environ;
static volatile pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

/* The daemon's environment: ours, minus anything that injects code into the loader or Python. */
static char **daemon_env(void) {
    size_t n = 0;
    while (environ[n]) n++;
    char **env = calloc(n + 2, sizeof(char *));
    if (!env) return NULL;
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        const char *e = environ[i];
        if (strncmp(e, "DYLD_", 5) == 0 || strncmp(e, "PYTHON", 6) == 0 || strncmp(e, "DESKTOP_SENSE_APP=", 18) == 0)
            continue;
        env[k++] = (char *)e;
    }
    env[k++] = "DESKTOP_SENSE_APP=1";  /* lets the daemon name this app in its permission hints */
    env[k] = NULL;
    return env;
}

int main(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    char **env = daemon_env();
    if (!env) return 1;
    char *args[] = {(char *)DS_PYTHON, (char *)DS_SCRIPT, "daemon", NULL};

    /* Hold signals until the child's pid is known, so a stop request can't slip past it. */
    sigset_t block, old, empty;
    sigemptyset(&block);
    sigaddset(&block, SIGTERM);
    sigaddset(&block, SIGINT);
    sigaddset(&block, SIGHUP);
    sigemptyset(&empty);
    sigprocmask(SIG_BLOCK, &block, &old);
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setsigmask(&attr, &empty);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK);
    pid_t pid = 0;
    int rc = posix_spawn(&pid, DS_PYTHON, NULL, &attr, args, env);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        fprintf(stderr, "desktop-sense: cannot start %s: %s\n", DS_PYTHON, strerror(rc));
        return 1;
    }
    child = pid;
    sigprocmask(SIG_SETMASK, &old, NULL);

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) return 1;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 128 + WTERMSIG(status);
}

/* desktop-sense launcher for macOS.
 *
 * macOS grants Screen Recording to the "responsible" app of a process, and a plain Python binary can't
 * even be added to that list in System Settings. So launchd starts this small program from
 * ~/Applications/desktop-sense.app (built by install.sh), and it starts the Python daemon as its child:
 * the permission prompt and the System Settings entry then say "desktop-sense".
 *
 * usage: desktop-sense <program> [args...]   e.g. desktop-sense /path/.venv/bin/python /path/ds.py daemon
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static volatile pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "desktop-sense: this app is started by `ds start`; it has nothing to do on its own.\n");
        return 2;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    setenv("DESKTOP_SENSE_APP", "1", 1);  /* lets the daemon name this app in its permission hints */

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
    int rc = posix_spawn(&pid, argv[1], NULL, &attr, &argv[1], environ);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        fprintf(stderr, "desktop-sense: cannot start %s: %s\n", argv[1], strerror(rc));
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

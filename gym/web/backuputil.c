/* Deliberately vulnerable SUID binary — tier-3 privilege escalation.
 * Unbounded stack read into a 64-byte buffer; a function pointer sits
 * directly above the buffer in the same struct and is called after the
 * read. Overwrite it with win() and the loot is yours. win() is never
 * called in normal operation. Synthetic lab target — the bug is the point. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

struct note {
    char buf[64];
    void (*on_save)(void);
};

static void win(void) {
    /* claim the SUID euid for real before system() — /bin/sh (dash) drops
     * euid when ruid != euid, which would silently neuter the loot read */
    setgid(0);
    setuid(0);
    system("/bin/cat /root/flag1.txt /root/notes.txt /root/api_svc_key 2>/dev/null");
}

static void noop(void) {
    /* default save hook: does nothing */
}

int main(void) {
    struct note n;
    int c;
    size_t i = 0;
    n.on_save = noop;
    setvbuf(stdout, NULL, _IONBF, 0);
    puts("backuputil v2.1 — enter maintenance note:");
    while ((c = getchar()) != '\n' && c != EOF) {
        n.buf[i++] = (char)c;   /* no bounds check */
    }
    n.buf[i < 64 ? i : 63] = '\0';
    printf("stored %zu bytes\n", i);
    n.on_save();
    return 0;
}

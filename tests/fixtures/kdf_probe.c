#include <CommonCrypto/CommonKeyDerivation.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* First KDF is 8 bytes; second is 32. Driver must not treat the first
 * pause as a 32-byte chat key, and must record each call's length. */
int main(int argc, char **argv) {
    unsigned char out[32];
    unsigned char salt[16];
    memset(salt, 0x11, sizeof salt);

    const char *short_pw = "shortpw!"; /* 8 */
    CCKeyDerivationPBKDF(kCCPBKDF2, short_pw, 8, salt, sizeof salt,
                         kCCPRFHmacAlgSHA1, 1, out, sizeof out);

    const char long_pw[32] = "0123456789abcdef0123456789abcdef";
    CCKeyDerivationPBKDF(kCCPBKDF2, long_pw, 32, salt, sizeof salt,
                         kCCPRFHmacAlgSHA1, 1, out, sizeof out);

    if (argc > 1 && strcmp(argv[1], "--sleep") == 0) {
        sleep(2);
    }
    return 0;
}

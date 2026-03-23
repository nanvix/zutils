/* Copyright(c) The Maintainers of Nanvix. */
/* Licensed under the MIT License. */

#include <stdio.h>
#include <string.h>
#include <zlib.h>

int main(void)
{
    const char *original = "Hello from Nanvix with zlib!";
    unsigned long src_len = (unsigned long)strlen(original) + 1;

    /* Compress. */
    unsigned long comp_len = compressBound(src_len);
    unsigned char compressed[256];
    int rc = compress(compressed, &comp_len, (const unsigned char *)original, src_len);
    if (rc != Z_OK) {
        printf("compress() failed: %d\n", rc);
        return 1;
    }

    /* Decompress. */
    unsigned char decompressed[256];
    unsigned long decomp_len = sizeof(decompressed);
    rc = uncompress(decompressed, &decomp_len, compressed, comp_len);
    if (rc != Z_OK) {
        printf("uncompress() failed: %d\n", rc);
        return 1;
    }

    /* Verify round-trip. */
    if (decomp_len != src_len || memcmp(original, decompressed, src_len) != 0) {
        printf("Round-trip mismatch!\n");
        return 1;
    }

    printf("zlib version: %s\n", zlibVersion());
    printf("Original:     %s (%lu bytes)\n", original, src_len);
    printf("Compressed:   %lu bytes\n", comp_len);
    printf("Decompressed: %s (%lu bytes)\n", (char *)decompressed, decomp_len);
    printf("Round-trip OK!\n");

    return 0;
}

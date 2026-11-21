#include <stdio.h>
#include <stdint.h>
#include <string.h>

/* 5 trits (each 0,1,2) -> 1 byte; 3^5 = 243 < 256, lossless */
static uint8_t pack5(const int t[5]) {
    int b = 0;
    for (int i = 0; i < 5; i++) b = b * 3 + t[i];      /* b in [0,242] */
    return (uint8_t)(((b * 256) + 242) / 243);        /* scale into [0,255] */
}

static void unpack5(uint8_t byte, int t[5]) {
    int b = byte;
    for (int i = 0; i < 5; i++) {
        b = b * 3;
        t[i] = b >> 8;        /* 0..2, no division */
        b &= 0xFF;
    }
}

/* 45-trit word = 9 trytes = 9 bytes */
static void pack45(const int t[45], uint8_t out[9]) {
    for (int g = 0; g < 9; g++) out[g] = pack5(t + 5 * g);
}
static void unpack45(const uint8_t in[9], int t[45]) {
    for (int g = 0; g < 9; g++) unpack5(in[g], t + 5 * g);
}

/* trit-level adder for unbalanced ternary (0,1,2) */
static int trit_add(int a, int b, int *carry) {
    int s = a + b + *carry;
    *carry = (s >= 3) ? 1 : 0;
    if (*carry) s -= 3;
    return s;
}

int main(void) {
    int fail = 0;

    printf("=== Test 1: 5-trit <-> byte lossless (exhaustive 3^5=243) ===\n");
    uint8_t seen[256] = {0};
    int used = 0, collisions = 0, err = 0;
    for (int b = 0; b < 243; b++) {
        int t[5], v = b;
        for (int i = 4; i >= 0; i--) { t[i] = v % 3; v /= 3; }
        uint8_t p = pack5(t);
        if (seen[p]) collisions++; else { seen[p] = 1; used++; }
        int u[5]; unpack5(p, u);
        if (memcmp(t, u, sizeof t) != 0) {
            if (err < 3)
                printf("  FAIL %d%d%d%d%d -> byte %u -> %d%d%d%d%d\n",
                       t[0],t[1],t[2],t[3],t[4],p,u[0],u[1],u[2],u[3],u[4]);
            err++;
        }
    }
    printf("  combos=243  failed=%d  collisions=%d  bytes_used=%d/256\n",
           err, collisions, used);
    fail += err + collisions;

    printf("\n=== Test 2: sample mappings ===\n");
    int samples[][5] = {{0,0,0,0,0},{0,0,0,0,1},{0,0,0,1,2},
                        {0,0,1,0,0},{0,1,2,0,1},{1,1,1,1,1},{2,2,2,2,2}};
    for (int i = 0; i < 7; i++) {
        uint8_t p = pack5(samples[i]);
        int u[5]; unpack5(p, u);
        printf("  %d%d%d%d%d -> byte %3u -> %d%d%d%d%d  %s\n",
               samples[i][0],samples[i][1],samples[i][2],samples[i][3],samples[i][4],
               p, u[0],u[1],u[2],u[3],u[4],
               memcmp(samples[i],u,sizeof u)==0 ? "OK":"FAIL");
    }

    printf("\n=== Test 3: 45-trit word (9 trytes) roundtrip ===\n");
    int t45[45], u45[45];
    uint8_t b9[9];
    for (int i = 0; i < 45; i++) t45[i] = (i * i + 1) % 3;
    pack45(t45, b9);
    unpack45(b9, u45);
    int e45 = memcmp(t45, u45, sizeof t45) != 0;
    printf("  45 trits -> 9 bytes -> 45 trits: %s\n", e45 ? "FAIL" : "PASS");
    printf("  packed bytes:"); for (int i = 0; i < 9; i++) printf(" %3u", b9[i]); printf("\n");
    fail += e45;

    printf("\n=== Test 4: trit adder with carry (unbalanced 0,1,2) ===\n");
    int carry = 0;
    for (int i = 0; i < 6; i++) {
        int a = i % 3, b = (i * 2) % 3, cin = carry;
        int s = trit_add(a, b, &carry);
        printf("  %d + %d + carry(%d) = %d, cout=%d\n", a, b, cin, s, carry);
    }

    printf("\n=== RESULT: %s (%d failures) ===\n", fail ? "FAIL" : "ALL PASS", fail);
    return fail ? 1 : 0;
}

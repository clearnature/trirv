#include <stdio.h>
#include <stdint.h>
static uint8_t pack5(const int t[5]) {
    int b = 0; for (int i = 0; i < 5; i++) b = b*3 + t[i];
    return (uint8_t)(((b*256)+242)/243);
}
static void unpack5(uint8_t byte, int t[5]) {
    int b = byte;
    for (int i = 0; i < 5; i++) { b = b*3; t[i] = b>>8; b &= 0xFF; }
}
int main(void) {
    /* 找出未被任何合法 5-trit 组合产生的 "空洞" 字节值 */
    int used[256] = {0};
    for (int b = 0; b < 243; b++) {
        int t[5], v = b;
        for (int i = 4; i >= 0; i--) { t[i] = v%3; v/=3; }
        used[pack5(t)] = 1;
    }
    printf("=== 空洞字节值 (0-255 中无法由合法 5-trit 打包得到) ===\n");
    int holes = 0;
    for (int b = 0; b < 256; b++) if (!used[b]) { printf("  %3u", b); holes++; }
    printf("\n共 %d 个空洞\n", holes);

    /* 关键: 任意 byte -> trit -> byte 是否闭合? (C char 可能取这些值) */
    printf("\n=== 任意 byte -> 5-trit -> byte 往返测试 ===\n");
    int broken = 0;
    for (int b = 0; b < 256; b++) {
        int t[5]; unpack5((uint8_t)b, t);
        uint8_t back = pack5(t);
        if (back != (uint8_t)b) {
            if (broken < 20) printf("  byte %3u -> trit %d%d%d%d%d -> byte %3u  [不闭合]\n",
                b,t[0],t[1],t[2],t[3],t[4],back);
            broken++;
        }
    }
    printf("不闭合的字节值共 %d/256 个\n", broken);
    return 0;
}

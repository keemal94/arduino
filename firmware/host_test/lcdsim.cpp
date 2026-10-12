// lcdsim: runs the firmware's UsageDisplay on the PC, driven by stdin, one command per line.
//
//   @<ms>   set the clock to <ms> (an absolute millis() value, taken modulo 2^32) and tick
//   #show   print the frame as four lines:
//             +----------------+
//             <row 0: exactly the 16 cells>
//             <row 1: exactly the 16 cells>
//             +----------------+
//   other   fed to the display byte by byte at the current clock, then a '\n',
//           as if received over serial (an empty line feeds just the '\n')
//
// Every line the display sends is printed as "TX <line>" when it is sent.
// The clock starts at 0 and should only move forward (or wrap). A trailing '\r' is
// stripped from @ and #show commands only. Bar cells (codes 0-5, the number of filled
// pixel columns) print as ░ ▎ ▍ ▋ ▊ █, or as the digits 0-5 with --raw.
// Exit status: 0, or 2 for a bad argument or a bad @ value.
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

#include "UsageDisplay.h"

static void printReply(const char* line) { std::printf("TX %s\n", line); }

int main(int argc, char** argv) {
  bool raw = false;
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--raw") == 0) {
      raw = true;
    } else {
      std::fprintf(stderr, "usage: lcdsim [--raw] < commands\n");
      return 2;
    }
  }
  static const char* const kCells[6] = {"░", "▎", "▍", "▋", "▊", "█"};
  UsageDisplay display(printReply);
  uint32_t now = 0;
  std::string line;
  while (std::getline(std::cin, line)) {
    std::string cmd = line;
    if (!cmd.empty() && cmd[cmd.size() - 1] == '\r') cmd.erase(cmd.size() - 1);
    if (!cmd.empty() && cmd[0] == '@') {
      char* end = nullptr;
      errno = 0;
      unsigned long long ms = std::strtoull(cmd.c_str() + 1, &end, 10);
      if (cmd.size() < 2 || *end != '\0' || errno || cmd[1] < '0' || cmd[1] > '9') {
        std::fprintf(stderr, "lcdsim: bad clock value: %s\n", cmd.c_str());
        return 2;
      }
      now = (uint32_t)ms;
      display.tick(now);
    } else if (cmd == "#show") {
      std::printf("+----------------+\n");
      for (uint8_t r = 0; r < 2; ++r) {
        for (int c = 0; c < 16; ++c) {
          uint8_t b = display.row(r)[c];
          if (b > 5) std::putchar(b);
          else if (raw) std::putchar('0' + b);
          else std::fputs(kCells[b], stdout);
        }
        std::putchar('\n');
      }
      std::printf("+----------------+\n");
    } else {
      for (char c : line) display.feed((uint8_t)c, now);
      display.feed('\n', now);
    }
    std::fflush(stdout);
  }
  return 0;
}

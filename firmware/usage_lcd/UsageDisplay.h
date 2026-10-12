// Protocol and screen logic of the usage LCD (docs/PROTOCOL.md, sections 2 and 3).
// Plain C++ with no Arduino dependency: the sketch and the host tests build the same code.
#ifndef USAGE_DISPLAY_H
#define USAGE_DISPLAY_H

#include <stdint.h>

class UsageDisplay {
public:
  typedef void (*ReplyFn)(const char* line);  // one line to send, without the '\n'

  explicit UsageDisplay(ReplyFn reply);

  // A received serial byte, with the current millis().
  void feed(uint8_t byte, uint32_t nowMs);
  // Advances countdowns, pages and the link timeout, then redraws the frame.
  void tick(uint32_t nowMs);
  // Sends the hello line.
  void hello();

  // The 16 cells of row 0 or 1: codes 0-5 are bar cells with that many of
  // their 5 pixel columns filled, everything else is ASCII.
  const uint8_t* row(uint8_t r) const { return frame_[r]; }

private:
  static const uint8_t kMaxLine = 63;
  enum Mode { kBoot, kWaiting, kData, kLost };

  void handleLine(uint32_t nowMs);
  void render();

  ReplyFn reply_;
  char line_[kMaxLine];
  uint8_t len_;
  bool tooLong_;
  uint8_t mode_;
  bool resetsPage_;
  int16_t left_[2];        // tenths of a percent left, -1 unknown
  int32_t secs_[2];        // seconds until reset, -1 unknown
  uint32_t lastLineMs_;    // last valid U or W line
  uint32_t countMs_;       // countdowns are current as of this time
  uint32_t pageMs_;        // start of the current bars+resets cycle
  uint8_t frame_[2][16];
};

#endif

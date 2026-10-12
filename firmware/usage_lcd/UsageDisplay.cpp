#include "UsageDisplay.h"

#include <string.h>

// AVR int is 16 bits: anything that can pass 32767 is computed in int32_t/uint32_t.
namespace {

const uint32_t kLinkTimeoutMs = 35000;
const uint32_t kBarsMs = 8000;
const uint32_t kCycleMs = 12000;  // bars page 8 s + resets page 4 s
const int32_t kMaxSecs = 2147483647;

void put(uint8_t* row, uint8_t col, const char* text) {
  memcpy(row + col, text, strlen(text));
}

// Writes text so that it ends in column 15.
void putRight(uint8_t* row, const char* text) {
  size_t n = strlen(text);
  memcpy(row + 16 - n, text, n);
}

char* putNum(char* p, uint32_t v, uint8_t minDigits) {
  char digits[10];
  uint8_t n = 0;
  do {
    digits[n++] = (char)('0' + v % 10);
    v /= 10;
  } while (v || n < minDigits);
  while (n) *p++ = digits[--n];
  return p;
}

// "4h05m": s in big units, then the rest in small units as two digits.
char* twoUnits(char* p, uint32_t s, uint32_t big, uint32_t small, char bigUnit, char smallUnit) {
  p = putNum(p, s / big, 1);
  *p++ = bigUnit;
  p = putNum(p, s % big / small, 2);
  *p++ = smallUnit;
  return p;
}

// out: at least 7 bytes. Formats as in PROTOCOL.md section 3, "Resets page".
void formatDuration(int32_t s, char* out) {
  char* p = out;
  if (s < 0) {
    memcpy(p, "--", 2);
    p += 2;
  } else if (s == 0) {
    memcpy(p, "now", 3);
    p += 3;
  } else if (s < 60) {
    p = putNum(p, (uint32_t)s, 1);
    *p++ = 's';
  } else if (s < 3600) {
    p = twoUnits(p, (uint32_t)s, 60, 1, 'm', 's');
  } else if (s < 86400L) {
    p = twoUnits(p, (uint32_t)s, 3600, 60, 'h', 'm');
  } else if (s < 8640000L) {
    p = twoUnits(p, (uint32_t)s, 86400L, 3600, 'd', 'h');
  } else {
    memcpy(p, "99d+", 4);
    p += 4;
  }
  *p = 0;
}

// Accepts "-1" or decimal digits worth at most maxVal; nothing else.
bool parseInt(const char* p, const char* end, int32_t maxVal, int32_t* out) {
  if (end - p == 2 && p[0] == '-' && p[1] == '1') {
    *out = -1;
    return true;
  }
  if (p == end) return false;
  int32_t v = 0;
  for (; p < end; ++p) {
    if (*p < '0' || *p > '9') return false;
    int32_t d = *p - '0';
    if (v > (maxVal - d) / 10) return false;
    v = v * 10 + d;
  }
  *out = v;
  return true;
}

// "U|l5|r5|l7|r7" into v[0..3].
bool parseUsage(const char* p, const char* end, int32_t* v) {
  if (p == end || *p++ != 'U') return false;
  for (uint8_t i = 0; i < 4; ++i) {
    if (p == end || *p != '|') return false;
    const char* field = ++p;
    while (p < end && *p != '|') ++p;
    if (!parseInt(field, p, (i & 1) ? kMaxSecs : 1000, &v[i])) return false;
  }
  return p == end;
}

}  // namespace

UsageDisplay::UsageDisplay(ReplyFn reply)
    : reply_(reply), len_(0), tooLong_(false), mode_(kBoot), resetsPage_(false),
      lastLineMs_(0), countMs_(0), pageMs_(0) {
  left_[0] = left_[1] = -1;
  secs_[0] = secs_[1] = -1;
  render();
}

void UsageDisplay::hello() { reply_("R|usage-lcd|1"); }

void UsageDisplay::feed(uint8_t byte, uint32_t nowMs) {
  if (byte == '\r') return;
  if (byte != '\n') {
    if (len_ < kMaxLine) line_[len_++] = (char)byte;
    else tooLong_ = true;
    return;
  }
  if (tooLong_) reply_("E|too-long");
  else if (len_) handleLine(nowMs);
  len_ = 0;
  tooLong_ = false;
}

void UsageDisplay::handleLine(uint32_t nowMs) {
  int32_t v[4];
  if (len_ == 1 && line_[0] == '?') {
    hello();
    return;
  }
  if (len_ == 1 && line_[0] == 'W') {
    mode_ = kWaiting;
  } else if (parseUsage(line_, line_ + len_, v)) {
    if (mode_ != kData) {
      mode_ = kData;
      pageMs_ = nowMs;  // start on the bars page
    }
    left_[0] = (int16_t)v[0];
    secs_[0] = v[1];
    left_[1] = (int16_t)v[2];
    secs_[1] = v[3];
    countMs_ = nowMs;
  } else {
    reply_("E|bad-line");
    return;
  }
  lastLineMs_ = nowMs;
  tick(nowMs);
}

void UsageDisplay::tick(uint32_t nowMs) {
  if ((mode_ == kWaiting || mode_ == kData) && nowMs - lastLineMs_ >= kLinkTimeoutMs) mode_ = kLost;
  if (mode_ == kData) {
    // Count down in whole seconds; countMs_ moves with them, so there is no drift.
    uint32_t secs = (nowMs - countMs_) / 1000;
    if (secs) {
      countMs_ += secs * 1000;
      for (uint8_t w = 0; w < 2; ++w) {
        if (secs_[w] <= 0) continue;  // unknown, or 0 as received
        if ((uint32_t)secs_[w] <= secs) {
          left_[w] = 1000;  // reached 0: the window has reset
          secs_[w] = -1;
        } else {
          secs_[w] -= (int32_t)secs;
        }
      }
    }
    uint32_t t = nowMs - pageMs_;
    if (t >= kCycleMs) {
      pageMs_ += t - t % kCycleMs;
      t %= kCycleMs;
    }
    resetsPage_ = t >= kBarsMs;
  }
  render();
}

void UsageDisplay::render() {
  memset(frame_, ' ', sizeof frame_);
  if (mode_ == kBoot) {
    put(frame_[0], 0, "Claude usage LCD");
    put(frame_[1], 0, "waiting for PC..");
    return;
  }
  if (mode_ == kWaiting) {
    put(frame_[0], 0, "Claude usage");
    put(frame_[1], 0, "waiting for data");
    return;
  }
  if (mode_ == kLost) {
    put(frame_[0], 0, "PC link lost");
    put(frame_[1], 0, "start the bridge");
    return;
  }
  for (uint8_t w = 0; w < 2; ++w) {
    uint8_t* row = frame_[w];
    int16_t l = left_[w];
    char text[7];
    put(row, 0, w ? "7d" : "5h");
    if (l == 0 || resetsPage_) {
      put(row, 2, l == 0 ? " LIMIT" : " resets");
      formatDuration(secs_[w], text);
      putRight(row, text);
      continue;
    }
    if (l > 0 && l <= 100) row[2] = '!';
    int32_t steps = 0;
    if (l > 0) {
      steps = ((int32_t)l * 45 + 500) / 1000;  // round half up
      if (steps < 1) steps = 1;
      if (l < 1000 && steps > 44) steps = 44;
    }
    for (uint8_t i = 0; i < 9; ++i) {
      int32_t f = steps - 5 * i;
      row[3 + i] = (uint8_t)(f <= 0 ? 0 : f >= 5 ? 5 : f);
    }
    if (l < 0) {
      putRight(row, "--%");
    } else {
      char* p = putNum(text, (uint32_t)(l / 10), 1);
      *p++ = '%';
      *p = 0;
      putRight(row, text);
    }
  }
}

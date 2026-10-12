// Host tests for UsageDisplay: exact frame text for every rule in docs/PROTOCOL.md sections 2 and 3.
// Frames are compared with bar cells written as the digits 0-5.
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "UsageDisplay.h"

static std::vector<std::string> sent;
static void capture(const char* line) { sent.push_back(line); }

static int failures = 0;
static int checks = 0;

static std::string rowText(const UsageDisplay& d, int r) {
  std::string s;
  for (int c = 0; c < 16; ++c) {
    uint8_t b = d.row((uint8_t)r)[c];
    s += b <= 5 ? (char)('0' + b) : (char)b;
  }
  return s;
}

#define EXPECT_EQ(actual, expected)                                                           \
  do {                                                                                        \
    ++checks;                                                                                 \
    std::string act_ = (actual), exp_ = (expected);                                           \
    if (act_ != exp_) {                                                                       \
      ++failures;                                                                             \
      std::printf("FAIL %s:%d\n  expected [%s]\n  actual   [%s]\n", __FILE__, __LINE__,       \
                  exp_.c_str(), act_.c_str());                                                \
    }                                                                                         \
  } while (0)

#define EXPECT_SCREEN(d, r0, r1) \
  do {                           \
    EXPECT_EQ(rowText(d, 0), r0); \
    EXPECT_EQ(rowText(d, 1), r1); \
  } while (0)

#define EXPECT_SENT(...)                                         \
  do {                                                           \
    const char* want_[] = {__VA_ARGS__};                         \
    std::string w_, g_;                                          \
    for (const char* s_ : want_) w_ += std::string(s_) + "\\n";  \
    for (const std::string& s_ : sent) g_ += s_ + "\\n";         \
    EXPECT_EQ(g_, w_);                                           \
    sent.clear();                                                \
  } while (0)

#define EXPECT_NOTHING_SENT()          \
  do {                                 \
    EXPECT_EQ(std::to_string(sent.size()), "0"); \
    sent.clear();                      \
  } while (0)

static void send(UsageDisplay& d, const std::string& bytes, uint32_t now) {
  for (char c : bytes) d.feed((uint8_t)c, now);
}
static void line(UsageDisplay& d, const std::string& text, uint32_t now) { send(d, text + "\n", now); }

// Expected bars-page row for l, from the contract's formulas.
static std::string barsRow(const char* label, int l) {
  std::string s = label;
  s += (l > 0 && l <= 100) ? '!' : ' ';
  int steps = 0;
  if (l > 0) {
    steps = (int)std::floor(l * 45 / 1000.0 + 0.5);
    if (steps < 1) steps = 1;
    if (l < 1000 && steps > 44) steps = 44;
  }
  for (int i = 0; i < 9; ++i) {
    int f = steps - 5 * i;
    s += (char)('0' + (f < 0 ? 0 : f > 5 ? 5 : f));
  }
  char pct[16];
  if (l < 0) std::snprintf(pct, sizeof pct, " --%%");
  else std::snprintf(pct, sizeof pct, "%3d%%", l / 10);
  return s + pct;
}

static void testBoot() {
  UsageDisplay d(capture);
  EXPECT_SCREEN(d, "Claude usage LCD", "waiting for PC..");
  d.tick(0);
  d.tick(100000);  // no line yet: never "link lost"
  EXPECT_SCREEN(d, "Claude usage LCD", "waiting for PC..");
  EXPECT_NOTHING_SENT();
  d.hello();
  EXPECT_SENT("R|usage-lcd|1");
  line(d, "?", 5);
  EXPECT_SENT("R|usage-lcd|1");
  EXPECT_SCREEN(d, "Claude usage LCD", "waiting for PC..");
}

static void testExamplePages() {
  UsageDisplay d(capture);
  const uint32_t t = 1000;
  line(d, "U|765|4980|410|266400", t);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  d.tick(t + 7999);
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  d.tick(t + 8000);  // 8 s counted down: 4972 s, 266392 s
  EXPECT_SCREEN(d, "5h resets  1h22m", "7d resets  3d01h");
  d.tick(t + 11999);
  EXPECT_SCREEN(d, "5h resets  1h22m", "7d resets  3d01h");
  d.tick(t + 12000);
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  d.tick(t + 20000);
  EXPECT_SCREEN(d, "5h resets  1h22m", "7d resets  3d01h");
}

static void testBarsAllValues() {
  for (int l = -1; l <= 1000; ++l) {
    if (l == 0) continue;  // LIMIT row, tested below
    UsageDisplay d(capture);
    line(d, "U|" + std::to_string(l) + "|-1|" + std::to_string(l) + "|-1", 0);
    EXPECT_SCREEN(d, barsRow("5h", l), barsRow("7d", l));
  }
  EXPECT_NOTHING_SENT();
}

static void testBarBoundaries() {
  UsageDisplay d(capture);
  line(d, "U|1|-1|100|-1", 0);  // round(0.045)=0 -> at least 1; round(4.5)=5 (half up)
  EXPECT_SCREEN(d, "5h!100000000  0%", "7d!500000000 10%");
  line(d, "U|101|-1|999|-1", 0);  // 4.545 -> 5; 44.955 -> 45 -> at most 44
  EXPECT_SCREEN(d, "5h 500000000 10%", "7d 555555554 99%");
  line(d, "U|1000|-1|-1|-1", 0);
  EXPECT_SCREEN(d, "5h 555555555100%", "7d 000000000 --%");
  line(d, "U|11|-1|12|-1", 0);  // 0.495 -> 0 -> 1; 0.54 -> 1
  EXPECT_SCREEN(d, "5h!100000000  1%", "7d!100000000  1%");
  line(d, "U|500|-1|988|-1", 0);  // 22.5 -> 23; 44.46 -> 44
  EXPECT_SCREEN(d, "5h 555530000 50%", "7d 555555554 98%");
  line(d, "U|9|-1|10|-1", 0);  // percent rounds down
  EXPECT_SCREEN(d, "5h!100000000  0%", "7d!100000000  1%");
}

static void testLimitRow() {
  UsageDisplay d(capture);
  line(d, "U|0|4980|0|-1", 0);
  EXPECT_SCREEN(d, "5h LIMIT   1h23m", "7d LIMIT      --");
  d.tick(8000);  // resets page: same rows
  EXPECT_SCREEN(d, "5h LIMIT   1h22m", "7d LIMIT      --");
  d.tick(12000);
  EXPECT_SCREEN(d, "5h LIMIT   1h22m", "7d LIMIT      --");
}

static void testDurations() {
  const struct {
    const char* secs;
    const char* shown;
  } cases[] = {
      {"-1", "    --"},      {"0", "   now"},      {"1", "    1s"},       {"59", "   59s"},
      {"60", " 1m00s"},      {"61", " 1m01s"},     {"3599", "59m59s"},   {"3600", " 1h00m"},
      {"3660", " 1h01m"},    {"86399", "23h59m"}, {"86400", " 1d00h"},  {"90000", " 1d01h"},
      {"8639999", "99d23h"}, {"8640000", "  99d+"}, {"2147483647", "  99d+"},
  };
  for (const auto& c : cases) {
    // LIMIT row shows the time on the bars page, before any counting.
    UsageDisplay d(capture);
    line(d, std::string("U|0|") + c.secs + "|0|" + c.secs, 0);
    EXPECT_SCREEN(d, std::string("5h LIMIT  ") + c.shown, std::string("7d LIMIT  ") + c.shown);
    // Resets page: a line received during it keeps the page and shows its value as is.
    UsageDisplay e(capture);
    line(e, "U|500|-1|500|-1", 0);
    e.tick(9000);
    line(e, std::string("U|500|") + c.secs + "|500|" + c.secs, 9000);
    EXPECT_SCREEN(e, std::string("5h resets ") + c.shown, std::string("7d resets ") + c.shown);
  }
  EXPECT_NOTHING_SENT();
}

static void testPageTiming() {
  UsageDisplay d(capture);
  const uint32_t t = 500;
  line(d, "U|500|-1|500|-1", t);
  const char* bars0 = "5h 555530000 50%";
  d.tick(t + 7999);
  EXPECT_EQ(rowText(d, 0), bars0);
  d.tick(t + 8000);
  EXPECT_EQ(rowText(d, 0), "5h resets     --");
  // Lines while in DATA mode do not restart the cycle.
  line(d, "U|500|-1|500|-1", t + 9000);
  EXPECT_EQ(rowText(d, 0), "5h resets     --");
  d.tick(t + 12000);
  EXPECT_EQ(rowText(d, 0), bars0);
  // Heartbeats every 10 s; the cycle keeps its phase over many periods and a skipped tick.
  for (uint32_t k = 2; k <= 6; ++k) line(d, "U|500|-1|500|-1", t + 10000 * k);
  d.tick(t + 60000 + 7999);
  EXPECT_EQ(rowText(d, 0), bars0);
  d.tick(t + 60000 + 8000);
  EXPECT_EQ(rowText(d, 0), "5h resets     --");
  // W then U starts again on the bars page.
  line(d, "W", t + 69000);
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
  line(d, "U|500|-1|500|-1", t + 69500);
  EXPECT_EQ(rowText(d, 0), bars0);
  d.tick(t + 69500 + 7999);
  EXPECT_EQ(rowText(d, 0), bars0);
  d.tick(t + 69500 + 8000);
  EXPECT_EQ(rowText(d, 0), "5h resets     --");
}

static void testCountdownAcrossWrap() {
  UsageDisplay d(capture);
  const uint32_t t = 0xFFFFF000u;  // millis() wraps 4096 ms later
  line(d, "U|0|10|500|-1", t);
  EXPECT_SCREEN(d, "5h LIMIT     10s", "7d 555530000 50%");
  d.tick(t + 999);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT     10s");
  d.tick(t + 1000);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT      9s");
  d.tick(t + 3999);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT      7s");
  d.tick(t + 5000);  // past the wrap
  EXPECT_EQ(rowText(d, 0), "5h LIMIT      5s");
  d.tick(t + 9999);
  EXPECT_SCREEN(d, "5h LIMIT      1s", "7d resets     --");
  d.tick(t + 10000);  // reached 0: l = 1000, r = -1 until the next line
  EXPECT_SCREEN(d, "5h resets     --", "7d resets     --");
  d.tick(t + 12000);
  EXPECT_SCREEN(d, "5h 555555555100%", "7d 555530000 50%");
  d.tick(t + 30000);
  EXPECT_SCREEN(d, "5h 555555555100%", "7d 555530000 50%");
  line(d, "U|0|10|500|-1", t + 31000);  // the next line replaces it
  EXPECT_EQ(rowText(d, 0), "5h LIMIT     10s");
}

static void testCountdownRules() {
  UsageDisplay d(capture);
  line(d, "U|0|100|-1|2", 500);
  EXPECT_SCREEN(d, "5h LIMIT   1m40s", "7d 000000000 --%");
  d.tick(1499);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT   1m40s");
  d.tick(1500);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT   1m39s");
  d.tick(2000);  // irregular ticks: no drift
  d.tick(2499);
  EXPECT_SCREEN(d, "5h LIMIT   1m39s", "7d 000000000 --%");
  d.tick(2500);  // unknown l with a countdown also resets to 1000 at 0
  EXPECT_SCREEN(d, "5h LIMIT   1m38s", "7d 555555555100%");
  d.tick(3499);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT   1m38s");
  d.tick(3500);
  EXPECT_EQ(rowText(d, 0), "5h LIMIT   1m37s");
  // r = 0 as received shows "now" until the next line.
  line(d, "U|0|0|200|0", 4000);
  EXPECT_SCREEN(d, "5h LIMIT     now", "7d 540000000 20%");
  d.tick(12000);
  EXPECT_SCREEN(d, "5h LIMIT     now", "7d resets    now");
  EXPECT_NOTHING_SENT();
}

static void testLinkLost() {
  UsageDisplay d(capture);
  const uint32_t t = 2000;
  line(d, "U|765|4980|410|266400", t);
  line(d, "garbage", t + 20000);  // invalid lines do not keep the link alive
  line(d, "?", t + 30000);        // nor do queries
  EXPECT_SENT("E|bad-line", "R|usage-lcd|1");
  d.tick(t + 34999);
  EXPECT_EQ(rowText(d, 0).substr(0, 3), "5h ");
  d.tick(t + 35000);
  EXPECT_SCREEN(d, "PC link lost    ", "start the bridge");
  d.tick(t + 100000);
  EXPECT_SCREEN(d, "PC link lost    ", "start the bridge");
  line(d, "U|765|4980|410|266400", t + 100000);
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  // Heartbeat keeps it alive.
  line(d, "U|765|4980|410|266400", t + 130000);
  d.tick(t + 164999);
  EXPECT_EQ(rowText(d, 0).substr(0, 3), "5h ");
  d.tick(t + 165000);
  EXPECT_SCREEN(d, "PC link lost    ", "start the bridge");
  // W also times out.
  line(d, "W", t + 200000);
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
  d.tick(t + 234999);
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
  d.tick(t + 235000);
  EXPECT_SCREEN(d, "PC link lost    ", "start the bridge");
  // Across the millis() wrap.
  UsageDisplay e(capture);
  line(e, "W", 0xFFFFC000u);
  e.tick(0xFFFFC000u + 34999);
  EXPECT_SCREEN(e, "Claude usage    ", "waiting for data");
  e.tick(0xFFFFC000u + 35000);
  EXPECT_SCREEN(e, "PC link lost    ", "start the bridge");
  EXPECT_NOTHING_SENT();
}

static void testBadLines() {
  const char* bad[] = {
      "U|1|2|3",          "U|1|2|3|4|5",     "U|1|2|3|4|",       "U||2|3|4",
      "U|1001|0|0|0",     "U|-2|0|0|0",      "U|-0|0|0|0",       "U|+1|0|0|0",
      "U| 1|0|0|0",       "U|1 |0|0|0",      "U|1.5|0|0|0",      "U|0x10|0|0|0",
      "U|1|2147483648|1|1", "U|1|99999999999|1|1", "U|1|-1|1|-5", "U|1|--1|1|1",
      "u|1|2|3|4",        "U 1|2|3|4",       "U",                "U|",
      "W|",               "W ",              " W",               "w",
      "??",               "? ",              "X",                "R|usage-lcd|1",
      "U|1|2|3|4\x01",    "U|\xff|0|0|0",
  };
  UsageDisplay d(capture);
  for (const char* b : bad) {
    line(d, b, 0);
    EXPECT_SENT("E|bad-line");
    EXPECT_SCREEN(d, "Claude usage LCD", "waiting for PC..");
  }
  line(d, "U|765|4980|410|266400", 0);
  for (const char* b : bad) {
    line(d, b, 100);
    EXPECT_SENT("E|bad-line");
    EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  }
  // A NUL byte inside a line is junk too.
  send(d, std::string("W\0", 2) + "\n", 200);
  EXPECT_SENT("E|bad-line");
  // Largest values and leading zeros are accepted.
  line(d, "U|1000|2147483647|0000|0002", 300);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "5h 555555555100%", "7d LIMIT      2s");
  // Empty lines are ignored.
  send(d, "\n\r\n\n", 400);
  EXPECT_NOTHING_SENT();
}

static void testLineLength() {
  UsageDisplay d(capture);
  // 63 characters: accepted (a valid U line padded with leading zeros).
  std::string u63 = "U|" + std::string(63 - 21, '0') + "765|4980|410|266400";
  EXPECT_EQ(std::to_string(u63.size()), "63");
  line(d, u63, 0);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  // 64 characters: discarded whole, screen unchanged.
  std::string u64 = "U|" + std::string(64 - 21, '0') + "500|4980|500|266400";
  line(d, u64, 10);
  EXPECT_SENT("E|too-long");
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  // One reply for a very long line, then the next line works.
  line(d, std::string(500, 'W'), 20);
  EXPECT_SENT("E|too-long");
  line(d, "W", 30);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
  // A 63-character bad line is a bad line, not a too-long one.
  line(d, std::string(63, 'x'), 40);
  EXPECT_SENT("E|bad-line");
  // \r does not count towards the length.
  line(d, u63 + "\r", 50);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
}

static void testCarriageReturns() {
  UsageDisplay d(capture);
  send(d, "?\r\n", 0);
  EXPECT_SENT("R|usage-lcd|1");
  send(d, "\rW\r\n", 0);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
  send(d, "U|765|49\r80|410|266400\r\n", 0);
  EXPECT_NOTHING_SENT();
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  // A line arriving in pieces.
  send(d, "U|1000|-1", 100);
  EXPECT_SCREEN(d, "5h 555555400 76%", "7d 555300000 41%");
  send(d, "|1000|-1\n", 200);
  EXPECT_SCREEN(d, "5h 555555555100%", "7d 555555555100%");
  // Several lines in one burst.
  send(d, "?\nW\n?\n", 300);
  EXPECT_SENT("R|usage-lcd|1", "R|usage-lcd|1");
  EXPECT_SCREEN(d, "Claude usage    ", "waiting for data");
}

int main() {
  testBoot();
  testExamplePages();
  testBarsAllValues();
  testBarBoundaries();
  testLimitRow();
  testDurations();
  testPageTiming();
  testCountdownAcrossWrap();
  testCountdownRules();
  testLinkLost();
  testBadLines();
  testLineLength();
  testCarriageReturns();
  std::printf("%d checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}

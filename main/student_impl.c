#include "student_impl.h"

// === STUDENT_CODE_BEGIN ===
// Simple debounce: require N consecutive identical samples (press or release)
bool debounce(bool sample) {
    static bool state = false;
    static int  cnt   = 0;
    const int   N     = 4; // 4 * 5ms = ~20ms

    if (sample == state) {
        cnt = 0; // stable
    } else {
        if (++cnt >= N) { state = sample; cnt = 0; }
    }
    return state;
}
// === STUDENT_CODE_END ===

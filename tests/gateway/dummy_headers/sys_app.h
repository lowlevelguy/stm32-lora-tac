#ifndef __MOCK_SYS_APP_H__
#define __MOCK_SYS_APP_H__

#ifdef __cplusplus
extern "C" {
#endif

/* No-op test stub for the application trace macro.
 *
 * The production sys_app.h pulls in UTIL_ADV_TRACE_* and a chain of trace
 * backend headers (stm32_adv_trace.h, utilities_conf.h, etc.), none of
 * which are available on the host unit-test build. For unit tests we
 * neutralize APP_LOG entirely -- the SUT's calls expand to (void)0 and the
 * trace backend never participates.
 *
 * Signature matches the production ENABLED form `APP_LOG(TS,...)` (one
 * named parameter plus variadic, per sys_app.h:67) so the SUT's call sites
 * such as `APP_LOG(TS_ON, "fmt", ...)` continue to compile unchanged. */
#define APP_LOG(TS, ...) (void)0

#ifdef __cplusplus
}
#endif

#endif /* __MOCK_SYS_APP_H__ */

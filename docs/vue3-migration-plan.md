# Vue 3 Migration Plan

Last updated: 2026-07-01

## Current State

- Frontend now runs Vue 3.5, Vue Router 4, Vuex 4, `vue-loader` 17, and `@vue/compiler-sfc`.
- `webpack-dev-server` has been upgraded to 5.2.5 and the frontend Node engine is now `>=18.12.0`.
- `npm audit --json` currently reports 0 vulnerabilities.
- Runtime bootstrap, Router 4, Vuex 4, loader/compiler, Vue 3 lifecycle names, and Vue 3 reactivity-helper cleanup are complete.
- Browser smoke has verified the unauthenticated app path: `/` redirects to `/login?redirect=/series`, the Vue app mounts, and browser error logs are empty.
- Authenticated product flows still need browser validation with backend/API data.

## Repeatable Audit

Use this command from `frontend/` before and after each migration slice:

```bash
npm run audit:vue2
```

Current baseline:

| Category | Count | Meaning |
| --- | ---: | --- |
| `vue-global-api` | 0 | `new Vue`, `Vue.use`, or `Vue.prototype` usage that must move to Vue 3 app instance APIs |
| `vue2-lifecycle` | 0 | `beforeDestroy` / `destroyed` lifecycle names that become `beforeUnmount` / `unmounted` |
| `vue2-reactivity-helper` | 0 | `this.$set` / `this.$delete` calls that should become direct reactive assignment/deletion after state shape review |
| `vuex-options-api` | 70 | Vuex store/helper usage retained on Vuex 4; inventory this if deciding whether to move to Pinia |
| `vue2-template-api` | 0 | No current `slot-scope`, `$listeners`, `.sync`, or `.native` matches found |

The audit script is an inventory, not a failing gate. A successful run means the baseline was collected, not that Vue 3 compatibility is complete.

## Completed Migration Slices

1. Runtime bootstrap slice:
   - Replace `new Vue(...)` with `createApp(...)`.
   - Move `$message`, `$confirm`, and `$alert` from `Vue.prototype` to `app.config.globalProperties`.
   - Switch `Vue.use(...)` plugin wiring to app-level `app.use(...)`.

2. Router and store slice:
   - Upgrade Vue Router 3 to Router 4 and adjust route creation.
   - Choose Vuex 4 for lowest rewrite risk, or Pinia for a larger but cleaner state migration.
   - Preserve existing module names first so page components do not migrate all at once.

3. Component compatibility slice:
   - Rename `beforeDestroy` to `beforeUnmount`.
   - Review every `$set` / `$delete` call, especially canvas runtime dictionaries, before replacing with direct assignment/deletion.
   - Keep project/canvas UI behavior visually aligned with `frontend/src/views/prompts/PromptList.vue`.

4. Compiler slice:
   - Replace `vue-template-compiler` with the Vue 3 compiler package.
   - Upgrade `vue-loader` to 17 after runtime/router/store bootstrapping compiles.
   - Rerun `npm run lint`, `npm run build`, `npm run audit:vue2`, and browser smoke after each slice.

5. Runtime compatibility slice:
   - Rename `beforeDestroy` to `beforeUnmount`.
   - Replace `this.$set` and `this.$delete` with direct reactive assignment/deletion.
   - Keep Vuex helper usage in place on Vuex 4 for low-risk compatibility.

## Validation Gate

Minimum gate for each slice:

- `npm run audit:vue2`
- `npm run lint`
- `npm run build`

Stronger gate for runtime/router/store slices:

- Start dev server with `npm run dev -- --no-open --host 127.0.0.1 --port <free-port>`.
- HTTP smoke `http://127.0.0.1:<free-port>/`.
- Browser smoke for login shell, series list, project detail canvas, prompt list, and model list.

Current completed checks:

- `npm run lint`
- `npm run build`
- `npm run audit:vue2 -- --json`
- `npm audit --json`
- `npm ls vue vue-router vuex vue-loader @vue/compiler-sfc --depth=0`
- dev-server compile and HTTP smoke on `127.0.0.1`
- Browser smoke for unauthenticated redirect/login shell with no captured browser errors

## Remaining Product Validation

- Authenticated browser smoke for series list, project detail canvas, prompt list, and model list.
- Backend-backed API smoke with valid credentials or seeded local data.
- Visual inspection of canvas-heavy pages after authenticated access is available.

## Do Not Do

- Do not run `npm audit fix --force` as the migration mechanism.
- Do not downgrade back to `vue-template-compiler` or `vue-loader` 15.
- Do not treat production audit passing as proof that UI behavior survived.

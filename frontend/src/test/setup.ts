// Vitest setup. vite.config.ts has pointed `setupFiles` here for a while, but
// the file itself was never committed, so every `npm test` run failed to start
// with "Cannot find module .../src/test/setup.ts".
//
// Registers @testing-library/jest-dom's matchers (toBeInTheDocument and
// friends), which is what the installed devDependency is for.
import '@testing-library/jest-dom/vitest'

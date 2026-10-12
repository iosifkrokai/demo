import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      exclude: ['node_modules', 'build', 'dist', 'coverage'],
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
      // Vitest's 5 s default is a latency assertion nobody wrote. The heavier
      // specs (the sidebar renders the whole planner and waits on mocked
      // fetches) take over a second each in jsdom, and on a loaded machine they
      // cross 5 s and go red without a single wrong expectation — measured on
      // the same commit at 43 s and 77 s of test time, all timeouts and no
      // assertion failures either way. The ceiling is here to catch a test that
      // hangs, so keep it well above what a slow-but-working one costs.
      testTimeout: 20000,
      hookTimeout: 20000,
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html'],
        exclude: [
          'node_modules/',
          'src/test-utils.ts',
          '**/*.d.ts',
          '**/*.config.*',
        ],
      },
    },
  })
);

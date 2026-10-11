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

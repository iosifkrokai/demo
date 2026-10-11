import { describe, it, expect } from 'vitest';
import { activeTabBeforeLoad } from './routes';

/** The router's thrown redirect, carrying navigation options a test can read back. */
interface ThrownRedirect {
  options: {
    to?: string;
    params?: { activeTab?: string };
    search?: { profile?: unknown; style?: unknown };
  };
}

const catchRedirect = (fn: () => void): ThrownRedirect => {
  try {
    fn();
  } catch (error) {
    return error as ThrownRedirect;
  }
  throw new Error('expected a redirect to be thrown');
};

describe('routes', () => {
  describe('activeTabRoute beforeLoad', () => {
    it('should redirect to directions with default profile for invalid tab', () => {
      const redirect = catchRedirect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'invalid' },
          search: {},
        })
      );

      expect(redirect.options.to).toBe('/$activeTab');
      expect(redirect.options.params).toEqual({ activeTab: 'directions' });
      expect(redirect.options.search?.profile).toBeTruthy();
    });

    it('should redirect with default profile when profile is missing', () => {
      const redirect = catchRedirect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'directions' },
          search: {},
        })
      );

      expect(redirect.options.params).toEqual({ activeTab: 'directions' });
      expect(redirect.options.search?.profile).toBeTruthy();
    });

    it('should redirect with default profile and preserve other search params', () => {
      const search = { style: 'custom' } as {
        profile?: string;
        style: string;
      };

      const redirect = catchRedirect(() =>
        activeTabBeforeLoad({ params: { activeTab: 'directions' }, search })
      );

      expect(redirect.options.params).toEqual({ activeTab: 'directions' });
      expect(redirect.options.search?.style).toBe('custom');
      expect(redirect.options.search?.profile).toBeTruthy();
    });

    it('should not redirect when profile is present', () => {
      expect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'directions' },
          search: { profile: 'car' },
        })
      ).not.toThrow();
    });

    it('should redirect a removed tab even when profile is present', () => {
      const redirect = catchRedirect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'isochrones' },
          search: { profile: 'truck' },
        })
      );

      expect(redirect.options.params).toEqual({ activeTab: 'directions' });
    });

    it('should redirect a removed tab while switching tabs', () => {
      const redirect = catchRedirect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'tiles' },
          search: { profile: 'car' },
        })
      );

      expect(redirect.options.params).toEqual({ activeTab: 'directions' });

      expect(() =>
        activeTabBeforeLoad({
          params: { activeTab: 'directions' },
          search: { profile: 'car' },
        })
      ).not.toThrow();
    });
  });
});

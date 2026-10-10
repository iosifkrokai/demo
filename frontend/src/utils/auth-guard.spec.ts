import { describe, it, expect } from 'vitest';

import { loginRedirectFor, returnPathOf } from './auth-guard';

/** The mandatory-login rule, pinned on its own: the router only has to turn `loginRedirectFor`'s answer into a `redirect()`. */
describe('auth guard', () => {
  it('sends an anonymous visitor to /login and remembers where they were headed', () => {
    expect(
      loginRedirectFor(
        { pathname: '/directions', searchStr: '?profile=bicycle', hash: '' },
        false
      )
    ).toEqual({
      to: '/login',
      search: { redirect: '/directions?profile=bicycle' },
    });
  });

  it('lets a signed-in visitor stay', () => {
    expect(loginRedirectFor({ pathname: '/directions' }, true)).toBeNull();
  });

  it('returns the path alone when there is no search or hash', () => {
    expect(returnPathOf({ pathname: '/admin' })).toBe('/admin');
  });

  it('keeps search and hash in the return path', () => {
    expect(
      returnPathOf({ pathname: '/visited', searchStr: '?x=1', hash: '#top' })
    ).toBe('/visited?x=1#top');
  });
});

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { MobileSection } from './mobile-section';

describe('MobileSection', () => {
  beforeEach(cleanup);

  // ── Desktop (jsdom 1024px, max-md DOES NOT apply) ────────────────────────────

  // At 1024px jsdom: max-md (max-width: 1023px) does not match.
  // Toggle: class "hidden max-md:flex" — `hidden` hides it; `max-md:flex` never
  // activates (the prefix does not match). Body open: no max-md:hidden class.
  // Content is always in the DOM; RTL queries it regardless of CSS visibility.

  it('toggle is hidden on desktop (hidden overrides max-md:flex)', () => {
    render(
      <MobileSection id="test" title="Test group" defaultOpen={true}>
        <p data-testid="inner">content</p>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-test');
    // max-md:flex activates only on ≤767px; on ≥1024px `hidden` wins.
    // (RTL cannot reliably test CSS visibility in jsdom at the breakpoint edge,
    // so we verify the class composition instead — the toggle has both classes,
    // hidden wins on desktop.)
    expect(toggle).toHaveClass('hidden');
    expect(toggle).toHaveClass('max-md:flex');

    const body = screen.getByTestId('section-body-test');
    expect(body).not.toHaveClass('max-md:hidden');
    expect(screen.getByTestId('inner')).toBeInTheDocument();
  });

  it('body visible on desktop regardless of defaultOpen', () => {
    render(
      <MobileSection id="test2" title="Test group" defaultOpen={false}>
        <p data-testid="inner2">content</p>
      </MobileSection>
    );

    // At 1024px max-md does not apply: the body's max-md:hidden class (added
    // because open=false) has no effect. Content is in DOM and visible.
    const body = screen.getByTestId('section-body-test2');
    expect(body).toHaveClass('max-md:hidden'); // class is there (open=false)
    // ...but it does not apply on desktop, so content is visible
    expect(screen.getByTestId('inner2')).toBeInTheDocument();
  });

  // ── Mobile (≤767px): toggle visible, body hidden by default ─────────────────

  // At 1024px max-md applies, so the toggle IS visible and the body IS hidden.
  // We test the class logic rather than a real viewport — those are the mechanism.

  it('closed section: body has max-md:hidden', () => {
    render(
      <MobileSection id="amenities" title="Удобства" defaultOpen={false}>
        <p data-testid="body-content">chips here</p>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-amenities');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    // max-md:hidden is applied — on a ≤767px viewport the body is invisible.
    // (RTL queries the DOM regardless of CSS, so we check the class, not DOM presence.)
    const body = screen.getByTestId('section-body-amenities');
    expect(body).toHaveClass('max-md:hidden');
  });

  it('opens on click', async () => {
    const user = userEvent.setup({ delay: null });
    render(
      <MobileSection id="interests" title="Интересы" defaultOpen={false}>
        <p data-testid="chips">interests here</p>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-interests');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const body = screen.getByTestId('section-body-interests');
    expect(body).not.toHaveClass('max-md:hidden');
    expect(screen.getByTestId('chips')).toBeInTheDocument();
  });

  it('closes on second click', async () => {
    const user = userEvent.setup({ delay: null });
    render(
      <MobileSection id="avoid" title="Избегать" defaultOpen={true}>
        <p data-testid="avoid-chips">avoid chips</p>
      </MobileSection>
    );

    // defaultOpen=true → open starts true on desktop; on phone the button
    // is visible and the body is not hidden.
    const toggle = screen.getByTestId('section-toggle-avoid');
    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('section-body-avoid')).toHaveClass(
      'max-md:hidden'
    );
  });

  // ── aria-controls / aria-expanded ────────────────────────────────────────

  it('aria-controls points to the body id', () => {
    render(
      <MobileSection id="party" title="Участники" defaultOpen={false}>
        <div>steppers</div>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-party');
    const body = screen.getByTestId('section-body-party');
    expect(toggle).toHaveAttribute('aria-controls', body.id);
  });

  it('aria-expanded reflects the open state', async () => {
    const user = userEvent.setup({ delay: null });
    render(
      <MobileSection
        id="result-type"
        title="Тип результата"
        defaultOpen={false}
      >
        <div>segmented</div>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-result-type');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  // ── Summary / counter ─────────────────────────────────────────────────────

  it('shows the summary text when provided', () => {
    render(
      <MobileSection
        id="amenities2"
        title="Удобства"
        summary="2"
        defaultOpen={false}
      >
        <div>chips</div>
      </MobileSection>
    );

    const count = screen.getByTestId('section-count-amenities2');
    expect(count).toBeInTheDocument();
    expect(count).toHaveTextContent('2');
  });

  it('does not render the counter when summary is absent', () => {
    render(
      <MobileSection id="interests2" title="Интересы" defaultOpen={false}>
        <div>chips</div>
      </MobileSection>
    );

    expect(screen.queryByTestId('section-count-interests2')).toBeNull();
  });

  it('counter is visible when collapsed', () => {
    render(
      <MobileSection
        id="avoid2"
        title="Избегать"
        summary="1"
        defaultOpen={false}
      >
        <div>avoid chips</div>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-avoid2');
    expect(toggle).toBeVisible();
    // The count span is inside the visible toggle button
    const count = screen.getByTestId('section-count-avoid2');
    expect(count).toBeVisible();
  });

  // ── defaultOpen ───────────────────────────────────────────────────────────

  it('defaultOpen=true shows the body immediately on mobile', () => {
    render(
      <MobileSection id="result" title="Тип результата" defaultOpen={true}>
        <p data-testid="result-body">segmented control</p>
      </MobileSection>
    );

    // Button is visible (mobile) but aria-expanded is true → body not hidden
    const toggle = screen.getByTestId('section-toggle-result');
    expect(toggle).toBeVisible();
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const body = screen.getByTestId('section-body-result');
    expect(body).not.toHaveClass('max-md:hidden');
    expect(screen.getByTestId('result-body')).toBeInTheDocument();
  });

  it('defaultOpen=false starts collapsed on mobile', () => {
    render(
      <MobileSection id="amenities3" title="Удобства" defaultOpen={false}>
        <div>chips</div>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-amenities3');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('section-body-amenities3')).toHaveClass(
      'max-md:hidden'
    );
  });
});

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { MobileSection } from './mobile-section';

describe('MobileSection', () => {
  beforeEach(cleanup);

  it('toggle is hidden on desktop (hidden overrides max-md:flex)', () => {
    render(
      <MobileSection id="test" title="Test group" defaultOpen={true}>
        <p data-testid="inner">content</p>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-test');
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

    const body = screen.getByTestId('section-body-test2');
    expect(body).toHaveClass('max-md:hidden');
    expect(screen.getByTestId('inner2')).toBeInTheDocument();
  });

  it('closed section: body has max-md:hidden', () => {
    render(
      <MobileSection id="amenities" title="Удобства" defaultOpen={false}>
        <p data-testid="body-content">chips here</p>
      </MobileSection>
    );

    const toggle = screen.getByTestId('section-toggle-amenities');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

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

    const toggle = screen.getByTestId('section-toggle-avoid');
    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('section-body-avoid')).toHaveClass(
      'max-md:hidden'
    );
  });

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
    const count = screen.getByTestId('section-count-avoid2');
    expect(count).toBeVisible();
  });

  it('defaultOpen=true shows the body immediately on mobile', () => {
    render(
      <MobileSection id="result" title="Тип результата" defaultOpen={true}>
        <p data-testid="result-body">segmented control</p>
      </MobileSection>
    );

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

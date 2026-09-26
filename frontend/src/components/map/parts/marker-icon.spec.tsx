import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MarkerIcon, markerColorForStop } from './marker-icon';

describe('MarkerIcon', () => {
  it('should render without crashing', () => {
    expect(() => render(<MarkerIcon />)).not.toThrow();
  });

  it('should render svg marker', () => {
    const { container } = render(<MarkerIcon />);

    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
  });

  it('should have correct svg dimensions', () => {
    const { container } = render(<MarkerIcon />);

    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('width', '35');
    expect(svg).toHaveAttribute('height', '45');
  });

  it('should render aria-label with number', () => {
    render(<MarkerIcon number="1" />);

    expect(screen.getByLabelText('Точка 1')).toBeInTheDocument();
  });

  it('should display number when provided', () => {
    render(<MarkerIcon number="5" />);

    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('should not display number when not provided', () => {
    const { container } = render(<MarkerIcon />);

    const numberDiv = container.querySelector('.absolute');
    expect(numberDiv).not.toBeInTheDocument();
  });

  describe('color variants — role colours, one meaning each', () => {
    it('defaults to the quiet intermediate stop', () => {
      const { container } = render(<MarkerIcon />);

      const wrapper = container.firstChild;
      expect(wrapper).toHaveClass('[&_path]:fill-[#717171]');
    });

    it('marks the first stop as ink', () => {
      const { container } = render(<MarkerIcon color="start" />);

      expect(container.firstChild).toHaveClass('[&_path]:fill-[#222222]');
    });

    it('marks the last stop with the accent, never an error red', () => {
      const { container } = render(<MarkerIcon color="finish" />);

      expect(container.firstChild).toHaveClass('[&_path]:fill-[#ff385c]');
      expect(container.firstChild).not.toHaveClass('[&_path]:fill-[#dc3545]');
    });

    it('keeps the tourist and an isochrone centre outside the role scheme', () => {
      const { container } = render(<MarkerIcon color="me" />);
      expect(container.firstChild).toHaveClass('[&_path]:fill-[#007bff]');

      const { container: iso } = render(<MarkerIcon color="iso" />);
      expect(iso.firstChild).toHaveClass('[&_path]:fill-[#6f42c1]');
    });

    it('maps every legacy alias onto its role colour', () => {
      const fillOf = (color: Parameters<typeof MarkerIcon>[0]['color']) => {
        const { container, unmount } = render(<MarkerIcon color={color} />);
        const cls = (container.firstChild as HTMLElement).className;
        unmount();
        return cls;
      };

      expect(fillOf('green')).toBe(fillOf('start'));
      expect(fillOf('grey')).toBe(fillOf('via'));
      expect(fillOf('red')).toBe(fillOf('finish'));
      expect(fillOf('blue')).toBe(fillOf('me'));
      expect(fillOf('purple')).toBe(fillOf('iso'));
    });
  });

  describe('markerColorForStop', () => {
    it('gives start / intermediate / finish for a route', () => {
      expect(markerColorForStop(0, 4)).toBe('start');
      expect(markerColorForStop(1, 4)).toBe('via');
      expect(markerColorForStop(2, 4)).toBe('via');
      expect(markerColorForStop(3, 4)).toBe('finish');
    });

    it('does not call a lone stop a finish', () => {
      expect(markerColorForStop(0, 1)).toBe('start');
    });

    it('does not call the first of two stops a finish', () => {
      expect(markerColorForStop(0, 2)).toBe('start');
      expect(markerColorForStop(1, 2)).toBe('finish');
    });
  });

  it('should apply custom className', () => {
    const { container } = render(<MarkerIcon className="custom-class" />);

    const wrapper = container.firstChild;
    expect(wrapper).toHaveClass('custom-class');
  });

  it('should have base classes for sizing', () => {
    const { container } = render(<MarkerIcon />);

    const wrapper = container.firstChild;
    expect(wrapper).toHaveClass('w-[35px]');
    expect(wrapper).toHaveClass('h-[45px]');
  });

  it('should have cursor-pointer class', () => {
    const { container } = render(<MarkerIcon />);

    const wrapper = container.firstChild;
    expect(wrapper).toHaveClass('cursor-pointer');
  });
});

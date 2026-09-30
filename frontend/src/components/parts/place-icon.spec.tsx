import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { categoryEmoji, PlaceIcon } from './place-icon';

afterEach(cleanup);

describe('categoryEmoji', () => {
  it('knows the agent’s category taxonomy', () => {
    expect(categoryEmoji('замок')).toBe('🏰');
    expect(categoryEmoji('  Костёл ')).toBe('⛪');
    expect(categoryEmoji('кафе')).toBe('☕');
    expect(categoryEmoji('остановка транспорта')).toBe('🚏');
  });

  it('has nothing to say about a hand-placed or unknown point', () => {
    expect(categoryEmoji(null)).toBeNull();
    expect(categoryEmoji('')).toBeNull();
    expect(categoryEmoji('неизвестная категория')).toBeNull();
  });
});

describe('PlaceIcon', () => {
  it('falls back to a muted pin without a category', () => {
    const { container } = render(<PlaceIcon />);
    // an svg pin, not a category face
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('shows the category face, with the category itself on hover', () => {
    render(<PlaceIcon category="музей" />);
    expect(screen.getByTitle('музей')).toHaveTextContent('🖼');
  });
});

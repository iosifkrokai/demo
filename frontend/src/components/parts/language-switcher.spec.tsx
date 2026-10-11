import { afterEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import i18n, { LANGUAGE_STORAGE_KEY } from '@/i18n';

import { LanguageSwitcher } from './language-switcher';

afterEach(async () => {
  await i18n.changeLanguage('ru');
  localStorage.clear();
  document.documentElement.lang = 'ru';
});

const text = (key: string) => String(i18n.t(key));

describe('переключатель языка интерфейса', () => {
  it('показывает, какой язык включён сейчас', () => {
    render(<LanguageSwitcher />);

    expect(screen.getByTestId('language-ru')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByTestId('language-en')).toHaveAttribute(
      'aria-pressed',
      'false'
    );
  });

  it('переключает интерфейс, а не только кнопку', async () => {
    render(<LanguageSwitcher />);

    await userEvent.setup().click(screen.getByTestId('language-en'));

    expect(screen.getByTestId('language-en')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(text('tabs.plan')).toBe('Plan');
    expect(text('ask.placeholder')).toBe('What would you like to see?');
  });

  it('запоминает выбор и говорит документу, на каком он языке', async () => {
    render(<LanguageSwitcher />);

    await userEvent.setup().click(screen.getByTestId('language-en'));

    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
    expect(document.documentElement.lang).toBe('en');
  });

  it('доступно называет каждый вариант на его собственном языке', () => {
    render(<LanguageSwitcher />);

    expect(screen.getByTestId('language-en')).toHaveAttribute('lang', 'en');
    expect(screen.getByTestId('language-en')).toHaveAccessibleName(/english/i);
    expect(screen.getByTestId('language-ru')).toHaveAccessibleName(/русский/i);
  });
});

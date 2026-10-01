import { useTranslation } from 'react-i18next';

import { LANGUAGES, setLanguage, type Language } from '@/i18n';
import { DENSITY } from '@/components/mobile/density';
import { cn } from '@/lib/utils';

/** What the button shows: the switch itself has to be readable in any language. */
const SHORT: Record<Language, string> = { ru: 'RU', en: 'EN' };

/**
 * The interface-language switch.
 *
 * Two buttons rather than a dropdown: there are two languages, and both names
 * are always visible — a tourist looking for English should not have to open a
 * menu that is written in Russian.
 */
export const LanguageSwitcher = ({ className }: { className?: string }) => {
  const { t, i18n } = useTranslation();
  const current: Language = i18n.language === 'en' ? 'en' : 'ru';

  return (
    <div
      role="group"
      aria-label={t('language.label')}
      data-testid="language-switcher"
      className={cn(
        'inline-flex items-center gap-0.5 rounded-full border border-border bg-card p-0.5',
        className
      )}
    >
      {LANGUAGES.map((lng) => (
        <button
          key={lng}
          type="button"
          lang={lng}
          data-testid={`language-${lng}`}
          aria-pressed={current === lng}
          // The visible label is «RU»/«EN» — short on purpose, but a screen
          // reader should hear the language's own name, not two letters.
          aria-label={t(`language.${lng}`)}
          title={t(`language.${lng}`)}
          onClick={() => setLanguage(lng)}
          className={cn(
            'h-7 min-w-8 rounded-full px-2 text-badge font-semibold uppercase transition-colors',
            DENSITY.languageItem,
            current === lng
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          {SHORT[lng]}
        </button>
      ))}
    </div>
  );
};

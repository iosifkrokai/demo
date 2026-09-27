import { describe, expect, it } from 'vitest';

import { en } from './en';
import { ru } from './ru';

/** Every leaf path of a dictionary, e.g. `tabs.planShort`. */
const paths = (node: Record<string, unknown>, prefix = ''): string[] =>
  Object.entries(node).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return value && typeof value === 'object'
      ? paths(value as Record<string, unknown>, path)
      : [path];
  });

const valueAt = (node: Record<string, unknown>, path: string): string =>
  path.split('.').reduce<unknown>((acc, key) => (acc as Record<string, unknown>)[key], node) as string;

describe('словари локализации', () => {
  it('английский не отстал от русского ни одним ключом', () => {
    expect(paths(en).sort()).toEqual(paths(ru).sort());
  });

  it('в английском не осталось русских строк', () => {
    // `language.ru` — исключение: «Русский» это имя языка, и в английском
    // интерфейсе кнопка обязана остаться на нём же.
    const untranslated = paths(en)
      .filter((path) => path !== 'language.ru')
      .filter((path) =>
        /[А-Яа-яЁё]/.test(valueAt(en as unknown as Record<string, unknown>, path))
      );

    // Русский текст в английском словаре — это непереведённая строка, а не мелочь:
    // именно так и выглядит «вроде сделали локализацию».
    expect(untranslated).toEqual([]);
  });

  it('плейсхолдеры подстановок совпадают в обоих языках', () => {
    const placeholders = (value: string) => value.match(/\{\{\w+\}\}/g) ?? [];
    const mismatched = paths(ru).filter(
      (path) =>
        placeholders(valueAt(ru as unknown as Record<string, unknown>, path)).join(',') !==
        placeholders(valueAt(en as unknown as Record<string, unknown>, path)).join(',')
    );

    expect(mismatched).toEqual([]);
  });
});

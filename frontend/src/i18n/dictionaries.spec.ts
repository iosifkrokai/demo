import { describe, expect, it } from 'vitest';

import { flatEn as en, flatRu as ru } from './index';

/** Every leaf path of a dictionary, e.g. `tabs.planShort`. */
const paths = (node: Record<string, unknown>, prefix = ''): string[] =>
  Object.entries(node).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return value && typeof value === 'object'
      ? paths(value as Record<string, unknown>, path)
      : [path];
  });

const valueAt = (node: Record<string, unknown>, path: string): string =>
  path
    .split('.')
    .reduce<unknown>(
      (acc, key) => (acc as Record<string, unknown>)[key],
      node
    ) as string;

/**
 * i18next picks a plural form by a suffix appended to the key, and the forms
 * differ by language: Russian needs one/few/many, English one/other. Comparing
 * raw key sets would call that a missing translation, so the suffix is stripped
 * here and the forms are checked on their own — a Russian string that only has
 * `_other` silently renders empty.
 */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const baseKey = (path: string) => path.replace(PLURAL_SUFFIX, '');
const isPlural = (path: string) => PLURAL_SUFFIX.test(path);
const unique = (items: string[]) => [...new Set(items)].sort();

describe('словари локализации', () => {
  it('английский не отстал от русского ни одним ключом', () => {
    expect(unique(paths(en).map(baseKey))).toEqual(
      unique(paths(ru).map(baseKey))
    );
  });

  it('у множественных форм есть все нужные формы в обоих языках', () => {
    const bases = unique(paths(ru).filter(isPlural).map(baseKey));

    const missing = bases.filter((base) => {
      const suffixes = (list: Record<string, unknown>) =>
        paths(list)
          .filter((path) => baseKey(path) === base)
          // От последнего подчёркивания: `_other` длиннее `_one`, фиксированная
          // ширина среза сравнивала бы половину слова.
          .map((path) => path.slice(path.lastIndexOf('_')));
      return (
        !['_one', '_few', '_many'].every((f) => suffixes(ru).includes(f)) ||
        !['_one', '_other'].every((f) => suffixes(en).includes(f))
      );
    });

    expect(missing).toEqual([]);
  });

  it('в английском не осталось русских строк', () => {
    // `language.ru` — исключение: «Русский» это имя языка, и в английском
    // интерфейсе кнопка обязана остаться на нём же.
    const untranslated = paths(en)
      .filter((path) => path !== 'language.ru')
      .filter((path) =>
        /[А-Яа-яЁё]/.test(
          valueAt(en as unknown as Record<string, unknown>, path)
        )
      );

    // Русский текст в английском словаре — это непереведённая строка, а не
    // мелочь: именно так и выглядит «вроде сделали локализацию».
    expect(untranslated).toEqual([]);
  });

  it('подстановки совпадают там, где ключи совпадают', () => {
    const placeholders = (value: string) =>
      (value.match(/\{\{\w+\}\}/g) ?? []).sort().join(',');
    const mismatched = paths(ru)
      .filter((path) => paths(en).includes(path))
      .filter(
        (path) =>
          placeholders(
            valueAt(ru as unknown as Record<string, unknown>, path)
          ) !==
          placeholders(valueAt(en as unknown as Record<string, unknown>, path))
      );

    expect(mismatched).toEqual([]);
  });
});

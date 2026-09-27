import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { PlacePhoto } from './place-photo';
import i18n from '@/i18n';

const PHOTO = {
  url: 'https://upload.wikimedia.org/wikipedia/commons/6/6a/grodna.jpg',
  author: 'Александр Липилин',
  license: 'CC BY-SA 3.0',
  source: 'https://commons.wikimedia.org/wiki/File:grodna.jpg',
};

afterEach(async () => {
  await i18n.changeLanguage('ru');
});

describe('фото точки', () => {
  it('без фото не рисует ничего — ни картинки, ни заглушки', () => {
    const { container } = render(<PlacePhoto photo={null} name="Старый замок" />);

    // Заглушка читалась бы как «ещё грузится» на большинстве точек, у которых
    // фото просто нет.
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('показывает картинку лениво и подписывает её именем точки', () => {
    render(<PlacePhoto photo={PHOTO} name="Старый замок" />);

    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', PHOTO.url);
    expect(img).toHaveAttribute('alt', 'Старый замок');
    // Фото ниже сгиба не должно тормозить открытие панели.
    expect(img).toHaveAttribute('loading', 'lazy');
  });

  it('под картинкой видно автора и лицензию — этого требует лицензия', () => {
    render(<PlacePhoto photo={PHOTO} name="Старый замок" />);

    const credit = screen.getByRole('link');
    expect(credit).toHaveTextContent('Александр Липилин');
    expect(credit).toHaveTextContent('CC BY-SA 3.0');
    // И ведёт на страницу файла, чтобы подпись можно было проверить.
    expect(credit).toHaveAttribute('href', PHOTO.source);
  });

  it('без страницы файла подпись остаётся, ссылка не выдумывается', () => {
    render(<PlacePhoto photo={{ ...PHOTO, source: null }} name="Старый замок" />);

    expect(screen.getByText(/Александр Липилин/)).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('на английском подпись английская, а имя автора не переводится', async () => {
    await i18n.changeLanguage('en');
    render(<PlacePhoto photo={PHOTO} name="Old Castle" />);

    const credit = screen.getByRole('link');
    expect(credit).toHaveTextContent(/^photo:/);
    // Имя собственное остаётся как в источнике — это подпись, а не слово.
    expect(credit).toHaveTextContent('Александр Липилин');
  });
});

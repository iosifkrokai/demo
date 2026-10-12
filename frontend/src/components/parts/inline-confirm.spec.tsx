import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { InlineConfirm } from './inline-confirm';

const question = 'Удалить место «Старый замок»?';

const renderConfirm = (onConfirm = vi.fn()) => {
  render(
    <InlineConfirm
      question={question}
      onConfirm={onConfirm}
      data-testid="delete"
    >
      <span>иконка</span>
    </InlineConfirm>
  );
  return onConfirm;
};

describe('InlineConfirm — a destructive action asks in place, not in a modal', () => {
  it('shows only the trigger until it is armed', () => {
    renderConfirm();

    expect(screen.getByTestId('delete')).toBeInTheDocument();
    expect(screen.queryByText(question)).not.toBeInTheDocument();
    expect(screen.queryByTestId('delete-confirm')).not.toBeInTheDocument();
  });

  it('swaps to a question with confirm and cancel once clicked', () => {
    renderConfirm();

    fireEvent.click(screen.getByTestId('delete'));

    expect(screen.getByText(question)).toBeInTheDocument();
    expect(screen.getByTestId('delete-confirm')).toHaveTextContent('Удалить');
    expect(screen.getByTestId('delete-cancel')).toHaveTextContent('Отменить');
    expect(screen.queryByTestId('delete')).not.toBeInTheDocument();
  });

  it('runs the action only after the confirm button is pressed', () => {
    const onConfirm = renderConfirm();

    fireEvent.click(screen.getByTestId('delete'));
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('delete-confirm'));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('cancelling backs out without running the action', () => {
    const onConfirm = renderConfirm();

    fireEvent.click(screen.getByTestId('delete'));
    fireEvent.click(screen.getByTestId('delete-cancel'));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId('delete')).toBeInTheDocument();
    expect(screen.queryByText(question)).not.toBeInTheDocument();
  });

  it('Escape backs out without running the action', () => {
    const onConfirm = renderConfirm();

    fireEvent.click(screen.getByTestId('delete'));
    fireEvent.keyDown(screen.getByRole('group'), { key: 'Escape' });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByText(question)).not.toBeInTheDocument();
  });

  it('puts focus on the confirm button so the safe choice is keyboard-reachable', () => {
    renderConfirm();

    fireEvent.click(screen.getByTestId('delete'));

    expect(screen.getByTestId('delete-confirm')).toHaveFocus();
  });

  it('gives an icon-only trigger an accessible name', () => {
    render(
      <InlineConfirm
        question={question}
        onConfirm={vi.fn()}
        label="Удалить"
        data-testid="delete"
      >
        <span aria-hidden="true">иконка</span>
      </InlineConfirm>
    );

    expect(screen.getByRole('button', { name: 'Удалить' })).toBeInTheDocument();
  });
});

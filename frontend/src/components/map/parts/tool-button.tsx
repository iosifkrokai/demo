import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface ToolButtonProps {
  title: string;
  icon: ReactNode;
  onClick: () => void;
  className?: string;
  disabled?: boolean;
  active?: boolean;
  'data-testid'?: string;
}

export function ToolButton({
  title,
  icon,
  onClick,
  className,
  disabled = false,
  active = false,
  'data-testid': testId,
}: ToolButtonProps) {
  return (
    <button
      type="button"
      aria-label={title}
      title={title}
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        'flex size-11 items-center justify-center rounded-full border-none p-0 transition',
        'outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        active
          ? 'bg-primary/15 ring-2 ring-primary'
          : 'bg-card ring-2 ring-foreground/10',
        disabled ? 'cursor-default opacity-40' : 'cursor-pointer',
        className
      )}
    >
      <span aria-hidden={true} className="flex">
        {icon}
      </span>
    </button>
  );
}

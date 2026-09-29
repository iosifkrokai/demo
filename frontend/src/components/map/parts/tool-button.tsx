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
        'outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ff385c]',
        className
      )}
      style={{
        width: '44px',
        height: '44px',
        backgroundColor: active ? '#ffe4ea' : '#ffffff',
        borderRadius: '4px',
        boxShadow: active ? '0 0 0 2px #ff385c' : '0 0 0 2px rgba(0,0,0,0.1)',
        border: 'none',
        cursor: disabled ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 0,
        opacity: disabled ? 0.4 : 1,
      }}
    >
      <span aria-hidden={true} style={{ display: 'flex' }}>
        {icon}
      </span>
    </button>
  );
}

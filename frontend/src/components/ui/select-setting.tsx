import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { AccessibleIcon } from '@radix-ui/react-accessible-icon';
import { HelpCircle } from 'lucide-react';

interface SelectOption {
  key: string;
  text: string;
  value: string;
}

interface SelectSettingProps {
  id: string;
  label: string;
  description: string;
  placeholder?: string;
  value: string;
  options: SelectOption[];
  onValueChange: (value: string) => void;
  /** When true, lay out label + select on a single row. */
  inline?: boolean;
}

export const SelectSetting = ({
  id,
  label,
  description,
  placeholder = 'Select an option',
  value,
  options,
  onValueChange,
  inline = false,
}: SelectSettingProps) => {
  const labelBlock = (
    <div className="flex items-center gap-1.5">
      <Label htmlFor={id} className="text-sm font-medium">
        {label}
      </Label>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="relative text-muted-foreground hover:text-foreground hover:bg-transparent after:absolute after:left-1/2 after:top-1/2 after:size-11 after:-translate-x-1/2 after:-translate-y-1/2 after:content-['']"
          >
            <AccessibleIcon label={`More info about ${label}`}>
              <HelpCircle className="size-3.5" />
            </AccessibleIcon>
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="top"
          className="max-h-[300px] w-64 overflow-y-auto p-3"
        >
          <p className="text-xs text-muted-foreground">{description}</p>
        </PopoverContent>
      </Popover>
    </div>
  );

  const selectControl = (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger id={id} className={inline ? 'w-auto' : 'w-full'}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent align={inline ? 'end' : 'start'}>
        {options.map((option) => (
          <SelectItem key={option.key} value={option.value}>
            {option.text}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  if (inline) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 py-1">
        {labelBlock}
        {selectControl}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 py-1">
      {labelBlock}
      {selectControl}
    </div>
  );
};

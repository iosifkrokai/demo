import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { AccessibleIcon } from '@radix-ui/react-accessible-icon';
import { HelpCircle } from 'lucide-react';

interface CheckboxSettingProps {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export const CheckboxSetting = ({
  id,
  label,
  description,
  checked,
  onCheckedChange,
}: CheckboxSettingProps) => {
  return (
    <div className="flex items-center justify-between py-1">
      <div className="flex items-center gap-2">
        <Checkbox id={id} checked={checked} onCheckedChange={onCheckedChange} />
        <Label htmlFor={id} className="text-sm font-medium cursor-pointer">
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
    </div>
  );
};

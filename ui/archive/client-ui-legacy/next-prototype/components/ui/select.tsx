'use client'

import * as React from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

type FilterSelectOption = {
  value: string
  label: string
  disabled?: boolean
  keywords?: string[]
}

type FilterSelectOptionInput = FilterSelectOption | string

type FilterSelectProps = Omit<
  React.ComponentProps<'input'>,
  'size' | 'value' | 'defaultValue' | 'onChange'
> & {
  options: FilterSelectOptionInput[]
  value?: string
  defaultValue?: string
  onValueChange?: (value: string) => void
  allowEmpty?: boolean
  emptyLabel?: string
  emptyValue?: string
  allowFreeInput?: boolean
  submitOnSelect?: boolean
  noResultsText?: string
  size?: 'sm' | 'default'
  triggerClassName?: string
  panelClassName?: string
  optionClassName?: string
  toggleAriaLabel?: string
}

function normalizeFilterOption(option: FilterSelectOptionInput): FilterSelectOption {
  if (typeof option === 'string') {
    return {
      value: option,
      label: option,
      keywords: [],
    }
  }

  return {
    value: String(option.value),
    label: String(option.label),
    disabled: Boolean(option.disabled),
    keywords: Array.isArray(option.keywords)
      ? option.keywords.map((keyword) => String(keyword))
      : [],
  }
}

function dedupeAndSortOptions(
  options: FilterSelectOptionInput[],
  config: {
    allowEmpty: boolean
    emptyLabel: string
    emptyValue: string
  },
) {
  const deduped = new Map<string, FilterSelectOption>()

  for (const option of options) {
    const normalized = normalizeFilterOption(option)
    if (!deduped.has(normalized.value)) {
      deduped.set(normalized.value, normalized)
    }
  }

  const sorted = Array.from(deduped.values()).sort((left, right) =>
    left.label.localeCompare(right.label, undefined, {
      numeric: true,
      sensitivity: 'base',
    }),
  )

  if (!config.allowEmpty) {
    return sorted
  }

  return [
    {
      value: config.emptyValue,
      label: config.emptyLabel,
      keywords: [],
    },
    ...sorted.filter((option) => option.value !== config.emptyValue),
  ]
}

function matchesFilterOption(option: FilterSelectOption, query: string) {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return true
  }

  const haystacks = [option.label, option.value, ...(option.keywords || [])]
  return haystacks.some((candidate) =>
    String(candidate).toLowerCase().includes(normalizedQuery),
  )
}

function getNextEnabledIndex(
  options: FilterSelectOption[],
  currentIndex: number,
  direction: 1 | -1,
) {
  if (options.length === 0) {
    return -1
  }

  let nextIndex = currentIndex
  for (let attempt = 0; attempt < options.length; attempt += 1) {
    nextIndex = (nextIndex + direction + options.length) % options.length
    if (!options[nextIndex]?.disabled) {
      return nextIndex
    }
  }

  return -1
}

function requestAssociatedFormSubmit(
  hiddenInput: HTMLInputElement | null,
  formId?: string,
) {
  if (formId) {
    const formElement = document.getElementById(formId)
    if (formElement instanceof HTMLFormElement) {
      formElement.requestSubmit()
      return
    }
  }

  hiddenInput?.form?.requestSubmit()
}

function Select({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Root>) {
  return <SelectPrimitive.Root data-slot="select" {...props} />
}

function SelectGroup({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Group>) {
  return <SelectPrimitive.Group data-slot="select-group" {...props} />
}

function SelectValue({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Value>) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

function SelectTrigger({
  className,
  size = 'default',
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger> & {
  size?: 'sm' | 'default'
}) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={cn(
        "border-input data-[placeholder]:text-muted-foreground [&_svg:not([class*='text-'])]:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 dark:hover:bg-input/50 flex w-fit items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 data-[size=default]:h-9 data-[size=sm]:h-8 *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-2 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDownIcon className="size-4 opacity-50" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectContent({
  className,
  children,
  position = 'popper',
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        className={cn(
          'bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 relative z-50 max-h-(--radix-select-content-available-height) min-w-[8rem] origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border shadow-md',
          position === 'popper' &&
            'data-[side=bottom]:translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1 data-[side=top]:-translate-y-1',
          className,
        )}
        position={position}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn(
            'p-1',
            position === 'popper' &&
              'h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)] scroll-my-1',
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn('text-muted-foreground px-2 py-1.5 text-xs', className)}
      {...props}
    />
  )
}

function SelectItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "focus:bg-accent focus:text-accent-foreground [&_svg:not([class*='text-'])]:text-muted-foreground relative flex w-full cursor-default items-center gap-2 rounded-sm py-1.5 pr-8 pl-2 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
        className,
      )}
      {...props}
    >
      <span className="absolute right-2 flex size-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <CheckIcon className="size-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

function SelectSeparator({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Separator>) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn('bg-border pointer-events-none -mx-1 my-1 h-px', className)}
      {...props}
    />
  )
}

function SelectScrollUpButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollUpButton>) {
  return (
    <SelectPrimitive.ScrollUpButton
      data-slot="select-scroll-up-button"
      className={cn(
        'flex cursor-default items-center justify-center py-1',
        className,
      )}
      {...props}
    >
      <ChevronUpIcon className="size-4" />
    </SelectPrimitive.ScrollUpButton>
  )
}

function SelectScrollDownButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollDownButton>) {
  return (
    <SelectPrimitive.ScrollDownButton
      data-slot="select-scroll-down-button"
      className={cn(
        'flex cursor-default items-center justify-center py-1',
        className,
      )}
      {...props}
    >
      <ChevronDownIcon className="size-4" />
    </SelectPrimitive.ScrollDownButton>
  )
}

function FilterSelect({
  options,
  value,
  defaultValue,
  onValueChange,
  allowEmpty = false,
  emptyLabel = 'All',
  emptyValue = '',
  allowFreeInput = false,
  submitOnSelect = false,
  noResultsText = 'No option found',
  size = 'default',
  className,
  triggerClassName,
  panelClassName,
  optionClassName,
  toggleAriaLabel = 'Toggle options',
  disabled = false,
  id,
  name,
  form,
  placeholder = 'Select an option',
  onBlur,
  onFocus,
  onKeyDown,
  autoFocus,
  required,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
  ...inputProps
}: FilterSelectProps) {
  const generatedId = React.useId()
  const inputId = id ?? `filter-select-${generatedId}`
  const listboxId = `${inputId}-listbox`
  const containerRef = React.useRef<HTMLDivElement>(null)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const hiddenInputRef = React.useRef<HTMLInputElement>(null)
  const closeTimerRef = React.useRef<number | null>(null)
  const focusOpenModeRef = React.useRef<'button' | 'input' | null>(null)
  const [open, setOpen] = React.useState(false)
  const [internalValue, setInternalValue] = React.useState(
    defaultValue ?? (allowEmpty ? emptyValue : ''),
  )
  const [query, setQuery] = React.useState('')
  const [dirtyQuery, setDirtyQuery] = React.useState(false)
  const [highlightedIndex, setHighlightedIndex] = React.useState(-1)

  const currentValue = value !== undefined ? value : internalValue

  const normalizedOptions = React.useMemo(
    () =>
      dedupeAndSortOptions(options, {
        allowEmpty,
        emptyLabel,
        emptyValue,
      }),
    [allowEmpty, emptyLabel, emptyValue, options],
  )

  const selectedOption = React.useMemo(() => {
    const matched = normalizedOptions.find((option) => option.value === currentValue)
    if (matched) {
      return matched
    }

    if (currentValue) {
      return {
        value: currentValue,
        label: currentValue,
        keywords: [],
      }
    }

    return null
  }, [currentValue, normalizedOptions])

  const filteredOptions = React.useMemo(() => {
    if (!allowFreeInput || !open || !dirtyQuery) {
      return normalizedOptions
    }

    return normalizedOptions.filter((option) =>
      matchesFilterOption(option, query),
    )
  }, [allowFreeInput, dirtyQuery, normalizedOptions, open, query])

  const activeDescendantId =
    highlightedIndex >= 0 && highlightedIndex < filteredOptions.length
      ? `${inputId}-option-${highlightedIndex}`
      : undefined

  const clearCloseTimer = React.useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }, [])

  const syncQueryToSelected = React.useCallback(() => {
    setQuery(selectedOption?.label ?? '')
    setDirtyQuery(false)
  }, [selectedOption])

  const commitValue = React.useCallback(
    (nextValue: string) => {
      if (value === undefined) {
        setInternalValue(nextValue)
      }
      onValueChange?.(nextValue)
    },
    [onValueChange, value],
  )

  const closePanel = React.useCallback(() => {
    setOpen(false)
    syncQueryToSelected()
  }, [syncQueryToSelected])

  const selectOption = React.useCallback(
    (option: FilterSelectOption) => {
      if (option.disabled || disabled) {
        return
      }

      commitValue(option.value)
      setQuery(option.label)
      setDirtyQuery(false)
      setOpen(false)

      requestAnimationFrame(() => {
        inputRef.current?.focus()
      })

      if (submitOnSelect) {
        requestAssociatedFormSubmit(hiddenInputRef.current, form)
      }
    },
    [commitValue, disabled, form, submitOnSelect],
  )

  const openPanel = React.useCallback(
    (source: 'button' | 'input') => {
      if (disabled) {
        return
      }

      clearCloseTimer()
      setOpen(true)
      setHighlightedIndex(
        filteredOptions.findIndex(
          (option) => option.value === currentValue && !option.disabled,
        ),
      )

      if (!allowFreeInput) {
        return
      }

      if (source === 'button') {
        setQuery('')
        setDirtyQuery(false)
        return
      }

      setQuery(selectedOption?.label ?? '')
      setDirtyQuery(false)
      requestAnimationFrame(() => {
        inputRef.current?.select()
      })
    },
    [
      allowFreeInput,
      clearCloseTimer,
      currentValue,
      disabled,
      filteredOptions,
      selectedOption,
    ],
  )

  React.useEffect(() => {
    if (!open) {
      syncQueryToSelected()
    }
  }, [open, syncQueryToSelected])

  React.useEffect(() => {
    if (disabled) {
      clearCloseTimer()
      setOpen(false)
    }
  }, [clearCloseTimer, disabled])

  React.useEffect(() => {
    if (!open) {
      return
    }

    const selectedIndex = filteredOptions.findIndex(
      (option) => option.value === currentValue && !option.disabled,
    )
    setHighlightedIndex(
      selectedIndex >= 0
        ? selectedIndex
        : getNextEnabledIndex(filteredOptions, -1, 1),
    )
  }, [currentValue, filteredOptions, open])

  React.useEffect(() => {
    return () => {
      clearCloseTimer()
    }
  }, [clearCloseTimer])

  const handleBlur = React.useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      onBlur?.(event as unknown as React.FocusEvent<HTMLInputElement>)

      const nextTarget = event.relatedTarget
      if (nextTarget instanceof Node && containerRef.current?.contains(nextTarget)) {
        return
      }

      clearCloseTimer()
      closeTimerRef.current = window.setTimeout(() => {
        closePanel()
      }, 120)
    },
    [clearCloseTimer, closePanel, onBlur],
  )

  const handleFocus = React.useCallback(
    (event: React.FocusEvent<HTMLInputElement>) => {
      clearCloseTimer()
      onFocus?.(event)
      const source = focusOpenModeRef.current ?? 'input'
      focusOpenModeRef.current = null
      openPanel(source)
    },
    [clearCloseTimer, onFocus, openPanel],
  )

  const handleKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      onKeyDown?.(event)
      if (event.defaultPrevented || disabled) {
        return
      }

      if (event.key === 'ArrowDown') {
        event.preventDefault()
        if (!open) {
          openPanel('button')
          return
        }
        setHighlightedIndex((currentIndex) =>
          getNextEnabledIndex(filteredOptions, currentIndex, 1),
        )
        return
      }

      if (event.key === 'ArrowUp') {
        event.preventDefault()
        if (!open) {
          openPanel('button')
          return
        }
        setHighlightedIndex((currentIndex) =>
          getNextEnabledIndex(filteredOptions, currentIndex, -1),
        )
        return
      }

      if (event.key === 'Enter') {
        if (!open) {
          return
        }
        event.preventDefault()
        const option = filteredOptions[highlightedIndex]
        if (option) {
          selectOption(option)
        }
        return
      }

      if (event.key === 'Escape') {
        if (!open) {
          return
        }
        event.preventDefault()
        closePanel()
        return
      }

      if (event.key === 'Tab') {
        closePanel()
      }
    },
    [
      closePanel,
      disabled,
      filteredOptions,
      highlightedIndex,
      onKeyDown,
      open,
      openPanel,
      selectOption,
    ],
  )

  const displayValue = allowFreeInput && open ? query : (selectedOption?.label ?? '')

  return (
    <div
      ref={containerRef}
      className={cn('relative w-full', className)}
      onBlur={handleBlur}
    >
      {name ? (
        <input
          ref={hiddenInputRef}
          type="hidden"
          name={name}
          value={currentValue}
          disabled={disabled}
          form={form}
        />
      ) : null}

      <div
        className={cn(
          'border-input bg-input/50 flex w-full items-center rounded-md border shadow-xs transition-[color,box-shadow]',
          'focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50',
          size === 'sm' ? 'min-h-8' : 'min-h-9',
          disabled && 'cursor-not-allowed opacity-50',
          triggerClassName,
        )}
      >
        <input
          {...inputProps}
          ref={inputRef}
          id={inputId}
          type="text"
          autoFocus={autoFocus}
          disabled={disabled}
          readOnly={!allowFreeInput}
          required={required}
          role="combobox"
          aria-label={ariaLabel}
          aria-labelledby={ariaLabelledBy}
          aria-controls={listboxId}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-autocomplete={allowFreeInput ? 'list' : 'none'}
          aria-activedescendant={open ? activeDescendantId : undefined}
          autoComplete="off"
          value={displayValue}
          placeholder={placeholder}
          onFocus={handleFocus}
          onKeyDown={handleKeyDown}
          onClick={() => openPanel('input')}
          onChange={(event) => {
            if (!allowFreeInput) {
              return
            }
            setQuery(event.target.value)
            setDirtyQuery(true)
            if (!open) {
              setOpen(true)
            }
          }}
          className={cn(
            'text-foreground placeholder:text-muted-foreground w-full min-w-0 bg-transparent px-3 py-2 text-sm outline-none',
            !allowFreeInput && 'cursor-pointer',
          )}
        />
        <button
          type="button"
          disabled={disabled}
          aria-label={toggleAriaLabel}
          className={cn(
            'text-muted-foreground border-input hover:bg-secondary focus-visible:ring-ring/50 flex h-full min-h-9 w-9 shrink-0 items-center justify-center border-l transition-[background-color,transform,box-shadow] outline-none focus-visible:ring-[3px]',
            size === 'sm' && 'min-h-8',
          )}
          onClick={() => {
            if (open) {
              closePanel()
              return
            }
            focusOpenModeRef.current = 'button'
            inputRef.current?.focus()
            openPanel('button')
          }}
        >
          <ChevronDownIcon
            className={cn(
              'size-4 transition-transform duration-150',
              open && 'rotate-180',
            )}
          />
        </button>
      </div>

      {open ? (
        <div
          className={cn(
            'bg-card text-foreground border-input absolute top-[calc(100%+4px)] left-0 z-50 w-full rounded-md border shadow-md',
            panelClassName,
          )}
        >
          <div
            id={listboxId}
            role="listbox"
            className="max-h-60 overflow-y-auto p-1"
          >
            {filteredOptions.length === 0 ? (
              <p className="text-muted-foreground px-3 py-2 text-sm">
                {noResultsText}
              </p>
            ) : (
              filteredOptions.map((option, index) => {
                const isSelected = option.value === currentValue
                const isHighlighted = index === highlightedIndex

                return (
                  <button
                    key={`${option.value}-${index}`}
                    id={`${inputId}-option-${index}`}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    disabled={option.disabled}
                    className={cn(
                      'flex min-h-9 w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm outline-none transition-colors',
                      'hover:bg-secondary focus-visible:bg-secondary focus-visible:ring-[3px] focus-visible:ring-ring/50',
                      (isHighlighted || isSelected) && 'bg-secondary',
                      isSelected && 'font-medium',
                      option.disabled && 'cursor-not-allowed opacity-50',
                      optionClassName,
                    )}
                    onMouseDown={(event) => {
                      event.preventDefault()
                    }}
                    onMouseEnter={() => {
                      if (!option.disabled) {
                        setHighlightedIndex(index)
                      }
                    }}
                    onClick={() => selectOption(option)}
                  >
                    <span className="truncate">{option.label}</span>
                    <CheckIcon
                      className={cn(
                        'text-muted-foreground size-4 shrink-0',
                        isSelected ? 'opacity-100' : 'opacity-0',
                      )}
                      aria-hidden="true"
                    />
                  </button>
                )
              })
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export {
  FilterSelect,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
}

export type { FilterSelectOption, FilterSelectOptionInput, FilterSelectProps }

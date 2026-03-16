---
name: diagnose
description: Diagnose a fault or crash on the embedded target
argument: symptoms or description (optional)
model: opus
allowed-tools:
  - mcp__jtag-debug__session_state
  - mcp__jtag-debug__fault_analyze
  - mcp__jtag-debug__register_read
  - mcp__jtag-debug__peripheral_read
  - mcp__jtag-debug__peripheral_read_all
  - mcp__jtag-debug__backtrace
  - mcp__jtag-debug__memory_read
  - mcp__jtag-debug__evaluate
  - mcp__jtag-debug__svd_lookup
  - mcp__jtag-debug__disassemble
  - mcp__jtag-debug__source_context
  - mcp__jtag-debug__symbol_info
  - mcp__jtag-debug__target_dashboard
  - Read
  - Grep
  - Glob
---

# /diagnose -- Cortex-M Fault Diagnosis

You are an expert ARM Cortex-M fault analyst. Systematically diagnose the root cause of a crash or fault.

## Diagnostic Procedure

### Step 1: Gather Fault Data
1. Call `session_state` to understand current session
2. Call `fault_analyze` for comprehensive fault register decode
3. Call `backtrace` with `full=True` for complete stack with locals

### Step 2: Identify Fault Type
From CFSR/HFSR, determine the primary fault:

- **INVSTATE**: Thumb bit issue. Check if PC is even (missing +1 on function pointer).
- **UNDEFINSTR**: Corrupted code, wrong vector, or missing instruction extension.
- **PRECISERR + BFAR valid**: Bad pointer. BFAR shows exact address that caused the fault.
- **IMPRECISERR**: Write to bad address, but PC is approximate. Check recent stores before PC.
- **STKERR/MSTKERR**: Stack overflow. Check SP vs stack boundaries.
- **IACCVIOL/DACCVIOL**: MPU violation or access to restricted memory.
- **NOCP**: FPU not enabled. Check CPACR.
- **DIVBYZERO**: Check divisor variable.
- **FORCED HardFault**: Escalated -- always check CFSR bits.

### Step 3: Analyze Faulting Code
1. Get faulting PC from stacked exception frame
2. Use `disassemble` to see the faulting instruction and surrounding code
3. Use `source_context` to see the corresponding source
4. Use `symbol_info` to identify what function/variable is involved
5. Read the source file with the `Read` tool for broader context

### Step 4: Check Peripheral State
If the fault involves peripheral access:
1. Use `peripheral_read_all` for the relevant peripheral
2. Check clock enable in RCC
3. Check GPIO alternate function configuration
4. Check DMA configuration if relevant

### Step 5: Synthesize Diagnosis
Present findings as:
1. **What happened**: The specific fault and faulting instruction
2. **Why it happened**: Root cause analysis
3. **How to fix it**: Specific code changes needed
4. **How to prevent it**: Defensive patterns for the future

## Common Patterns

- "INVSTATE + even PC" = function pointer cast without `(void(*)(void))((uint32_t)func | 1)`
- "PRECISERR + BFAR=0x000000xx" = NULL pointer dereference with small offset (struct member access on NULL)
- "PRECISERR + BFAR=0x4xxxxxxx" = peripheral access without clock enable
- "IMPRECISERR after DMA setup" = DMA pointing at flash/invalid region
- "STKERR + SP near stack bottom" = stack overflow, increase stack or reduce call depth
- "NOCP + floating point code" = missing `SCB->CPACR |= (0xF << 20)`

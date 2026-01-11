"""
Input Handler - Async input handling and validation.

This module handles user input with async support.
NO business logic, only input/output operations.
"""

import asyncio
import sys
from typing import Optional, List, Tuple, Any
from concurrent.futures import ThreadPoolExecutor


# Thread pool for blocking input operations
_executor = ThreadPoolExecutor(max_workers=1)


async def async_input(prompt: str = "") -> str:
    """
    Async wrapper for blocking input() function.
    
    Uses ThreadPoolExecutor to run input() in a separate thread,
    allowing other async tasks to continue.
    
    Args:
        prompt: Input prompt to display
    
    Returns:
        User input string (stripped)
    """
    loop = asyncio.get_event_loop()
    
    # Print prompt without newline
    if prompt:
        print(prompt, end="", flush=True)
    
    # Run blocking input in thread pool
    result = await loop.run_in_executor(_executor, sys.stdin.readline)
    return result.strip()


async def get_menu_choice(
    prompt: str,
    valid_choices: List[str],
    allow_empty: bool = False
) -> Optional[str]:
    """
    Get validated menu choice from user.
    
    Args:
        prompt: Input prompt
        valid_choices: List of valid choice strings (e.g., ["1", "2", "3"])
        allow_empty: Whether empty input is allowed
    
    Returns:
        Valid choice string, or None if cancelled
    """
    while True:
        user_input = await async_input(prompt)
        
        if not user_input and allow_empty:
            return ""
        
        if user_input.lower() in ['q', 'quit', 'exit']:
            return None
        
        if user_input in valid_choices:
            return user_input
        
        print(f"Invalid choice. Please select from: {', '.join(valid_choices)}")


async def get_integer_input(
    prompt: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    default: Optional[int] = None
) -> Optional[int]:
    """
    Get validated integer input.
    
    Args:
        prompt: Input prompt
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        default: Default value if user enters nothing
    
    Returns:
        Valid integer, or None if cancelled
    """
    while True:
        user_input = await async_input(prompt)
        
        if not user_input:
            if default is not None:
                return default
            continue
        
        if user_input.lower() in ['q', 'quit', 'cancel']:
            return None
        
        try:
            value = int(user_input)
            
            if min_val is not None and value < min_val:
                print(f"Value must be at least {min_val}")
                continue
            
            if max_val is not None and value > max_val:
                print(f"Value must be at most {max_val}")
                continue
            
            return value
            
        except ValueError:
            print("Please enter a valid number")


async def get_float_input(
    prompt: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    default: Optional[float] = None
) -> Optional[float]:
    """
    Get validated float input.
    
    Args:
        prompt: Input prompt
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        default: Default value if user enters nothing
    
    Returns:
        Valid float, or None if cancelled
    """
    while True:
        user_input = await async_input(prompt)
        
        if not user_input:
            if default is not None:
                return default
            continue
        
        if user_input.lower() in ['q', 'quit', 'cancel']:
            return None
        
        try:
            value = float(user_input)
            
            if min_val is not None and value < min_val:
                print(f"Value must be at least {min_val}")
                continue
            
            if max_val is not None and value > max_val:
                print(f"Value must be at most {max_val}")
                continue
            
            return value
            
        except ValueError:
            print("Please enter a valid number")


async def get_string_input(
    prompt: str,
    required: bool = True,
    validator: Optional[callable] = None,
    transform: Optional[callable] = None
) -> Optional[str]:
    """
    Get validated string input.
    
    Args:
        prompt: Input prompt
        required: Whether input is required
        validator: Optional validation function (returns bool)
        transform: Optional transform function (e.g., str.upper)
    
    Returns:
        Valid string, or None if cancelled
    """
    while True:
        user_input = await async_input(prompt)
        
        if user_input.lower() in ['q', 'quit', 'cancel']:
            return None
        
        if not user_input:
            if not required:
                return ""
            print("This field is required")
            continue
        
        # Apply transform
        if transform:
            user_input = transform(user_input)
        
        # Validate
        if validator and not validator(user_input):
            print("Invalid input, please try again")
            continue
        
        return user_input


async def get_confirmation(
    prompt: str,
    default: bool = True
) -> bool:
    """
    Get yes/no confirmation from user.
    
    Args:
        prompt: Confirmation prompt (should include [Y/n] or [y/N])
        default: Default value if user presses Enter
    
    Returns:
        True for yes, False for no
    """
    user_input = await async_input(prompt)
    
    if not user_input:
        return default
    
    return user_input.lower() in ['y', 'yes', 'да', '1']


async def get_symbol_input() -> Optional[str]:
    """
    Get trading pair symbol from user.
    
    Returns:
        Validated symbol (uppercase), or None if cancelled
    """
    return await get_string_input(
        prompt="Enter trading pair (e.g., BTCUSDT): ",
        required=True,
        transform=lambda s: s.upper().replace("/", "").replace(":", "")
    )


async def wait_for_keypress(message: str = "Press Enter to continue...") -> None:
    """Wait for user to press Enter"""
    await async_input(message)


async def get_exchange_selection(
    exchange_names: List[str],
    prompt: str
) -> Optional[int]:
    """
    Get exchange selection from list.
    
    Args:
        exchange_names: List of available exchange names
        prompt: Selection prompt
    
    Returns:
        Index of selected exchange (0-based), or None if cancelled
    """
    valid_choices = [str(i) for i in range(1, len(exchange_names) + 1)]
    valid_choices.append("0")  # Allow back/cancel
    
    choice = await get_menu_choice(prompt, valid_choices)
    
    if choice is None or choice == "0":
        return None
    
    return int(choice) - 1


class InterruptibleTask:
    """
    Wrapper for tasks that can be interrupted by user input.
    Used for hit-the-bid search and similar operations.
    """
    
    def __init__(self):
        self.interrupted = False
        self._check_task: Optional[asyncio.Task] = None
    
    async def start_interrupt_listener(self):
        """Start listening for 'q' press to interrupt"""
        self._check_task = asyncio.create_task(self._listen_for_interrupt())
    
    async def _listen_for_interrupt(self):
        """Background task to listen for interrupt key"""
        try:
            while not self.interrupted:
                # Non-blocking check with timeout
                try:
                    user_input = await asyncio.wait_for(
                        async_input(""),
                        timeout=0.1
                    )
                    if user_input.lower() == 'q':
                        self.interrupted = True
                        break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
    
    def stop(self):
        """Stop interrupt listener"""
        if self._check_task:
            self._check_task.cancel()
    
    def is_interrupted(self) -> bool:
        """Check if task was interrupted"""
        return self.interrupted

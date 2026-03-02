import ipywidgets as widgets
from IPython.display import display, clear_output, HTML
from datetime import datetime, timedelta
import math

# Define the full color palette at module level
FULL_PALETTE = [
    '#20313F', '#213D4B', '#204A56', '#1D5760', '#1C6568', 
    '#1C736E', '#238073', '#2E8E76', '#3E9C77', '#50A976', 
    '#66B674', '#7DC371', '#97CF6D', '#B2DA69', '#D0E567'
]

# Create specific segments for each metric
DAYS_PALETTE = FULL_PALETTE[:-2]  # Dark blue to medium green (#20313F -> #2D8D76)
FREQ_PALETTE = FULL_PALETTE[1:-1]  # Teal to light green (#1F4E59 -> #6EBB73)
RELATIVE_PALETTE = FULL_PALETTE[2:]  # Medium green to yellow (#2D8D76 -> #EFEE66)

# Define fixed ranges
STATS_RANGES = {
    'days_min': -7,     # Due now
    'days_max': 14,    # Due in 2 weeks
    'freq_min': 0,     # Daily tasks
    'freq_max': 50,    # Monthly tasks
    'relative_min': -0.25,  # Significantly overdue
    'relative_max': 1.0    # Ahead of schedule
}

class CadenceTable:
    def __init__(self, manager, items_per_page=25):
        """
        Initialize the CadenceTable with a manager instance.

        Parameters:
            manager: CadenceManager instance
            items_per_page: Number of items to display per page (default: 25)
        """
        self.manager = manager
        self.page_size = items_per_page
        
        # State variables
        self.breadcrumb_path = []
        self.current_page = 0
        self.name_filter = ""
        self.parent_filter = ""
        self.include_children = True
        
        # Output containers
        self.table_container = widgets.Output()
        self.notification_area = widgets.Output()
        self.breadcrumb_container = widgets.Output()
        self.search_container = widgets.Output()
        
        # Main layout
        self.main_layout = widgets.VBox([
            self.breadcrumb_container,
            self.search_container,
            self.table_container,
            self.notification_area
        ], layout=widgets.Layout(width='100%'))

    def has_children(self, chore_name):
        """
        Returns True if the given chore has at least one child (based on the parent_chores table).
        """
        cur = self.manager.connection.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM parent_chores WHERE parent_chore = ?", (chore_name,))
        result = cur.fetchone()
        return result["cnt"] > 0 if result else False

    def get_children_details(self, parent_name, visited=None):
        """
        Given a parent chore name, queries the database and returns the sorted details of all
        child chores, avoiding cycles by using the 'visited' set.
        """
        if visited is None:
            visited = set()
        if parent_name in visited:
            return []
        
        # Don't add the parent to visited yet - we need to query its children first
        cur = self.manager.connection.cursor()
        cur.execute("SELECT chore_name FROM parent_chores WHERE parent_chore = ?", (parent_name,))
        child_rows = cur.fetchall()
        
        # Get all children first, then filter out visited ones
        children = [row["chore_name"] for row in child_rows]
        unvisited_children = [child for child in children if child not in visited]
        
        # Now add the parent to visited
        visited.add(parent_name)
        
        # Use the manager's sorted chore details and filter for those matching the child names
        all_sorted = self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due")
        child_details = [detail for detail in all_sorted if detail["name"] in unvisited_children]
        return child_details

    def get_sequential_color(self, value, min_val, max_val, palette, reverse=False):
        """
        Returns a color from a sequential palette based on the value's position in the range.
        Each palette is a list of colors from light to dark.
        If reverse=True, the palette is applied in reverse (high values get light colors).
        """
        # Safety check - if palette is empty, return a default color
        if not palette:
            return "#CCCCCC"
        
        if value is None or not isinstance(value, (int, float)) or math.isnan(value):
            return palette[0] if reverse else palette[-1]
        
        # Clamp value to range
        value = max(min_val, min(value, max_val))
        
        # Calculate normalized position
        if min_val == max_val:
            normalized = 0.5
        else:
            normalized = (value - min_val) / (max_val - min_val)
        
        if reverse:
            normalized = 1 - normalized
        
        # Get palette index, ensuring we don't exceed bounds
        index = int(normalized * (len(palette) - 1))
        index = max(0, min(index, len(palette) - 1))
        
        return palette[index]

    def format_number(self, value):
        """Format numbers with K/M suffixes for large values, or to 2 decimal places"""
        if value is None:
            return "N/A"  # Return "N/A" for None values
        elif value >= 1e6:
            return f'{value/1e6:.1f}M'
        elif value >= 1e3:
            return f'{value/1e3:.1f}K'
        else:
            return f'{value:.2f}'

    def truncate_text(self, text, max_length=40):
        """Truncates text if it's longer than max_length and adds an ellipsis"""
        if len(text) > max_length:
            return text[:max_length-3] + "..."
        return text

    def create_chore_row(self, chore_detail, index, stats_ranges, on_expand=None, has_children=None, note_count=None, log_data=None, chore_column_width="300px"):
        """
        Creates a single row for a chore with optimized rendering.
        Combines all data cells into a single HTML widget to reduce widget overhead.
        """
        import base64

        name = chore_detail["name"]
        days_until_due = chore_detail["days_until_due"]
        frequency = chore_detail["frequency_in_days"]
        time_since = chore_detail.get("time_since_last_log", 0) or 0

        # Calculate the "relative" value
        frequency_safe = 0.001 if frequency is None or frequency <= 0 else frequency
        relative_ratio = (frequency_safe - time_since) / frequency_safe

        # Calculate colors
        days_color = self.get_sequential_color(days_until_due, STATS_RANGES['days_min'], STATS_RANGES['days_max'], DAYS_PALETTE, reverse=True) if days_until_due is not None else "#CCCCCC"
        freq_color = self.get_sequential_color(frequency, STATS_RANGES['freq_min'], STATS_RANGES['freq_max'], FREQ_PALETTE) if frequency is not None else "#CCCCCC"
        relative_color = self.get_sequential_color(relative_ratio, STATS_RANGES['relative_min'], STATS_RANGES['relative_max'], RELATIVE_PALETTE, reverse=True)

        def get_text_color(bg_color):
            if bg_color.startswith('#'):
                r, g, b = int(bg_color[1:3], 16), int(bg_color[3:5], 16), int(bg_color[5:7], 16)
                return 'white' if (r * 299 + g * 587 + b * 114) / 1000 < 128 else 'black'
            return 'black'

        # Get dates from chore_detail (already computed by get_sorted_due_chores)
        last_logged_dt = None
        next_due_dt = None
        try:
            if chore_detail.get('last_logged'):
                last_logged_dt = datetime.fromisoformat(chore_detail['last_logged'])
            if chore_detail.get('next_due'):
                next_due_dt = datetime.fromisoformat(chore_detail['next_due'])
        except (ValueError, TypeError):
            pass

        def format_date_consistent(dt):
            if dt is None:
                return "N/A"
            current_year = datetime.now().year
            if dt.year == current_year:
                return f"{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}"
            return f"{dt.month}/{dt.day}/{str(dt.year)[-2:]}"

        # Prepare values
        truncated_name = self.truncate_text(name, max_length=40)
        html_escaped_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
        encoded_name = base64.b64encode(name.encode('utf-8')).decode('ascii')
        onclick_handler = f"(function(){{{{try{{{{const name=atob('{encoded_name}'); navigator.clipboard.writeText(name).then(()=>console.log('Copied:',name)).catch(err=>console.error('Failed to copy:',err));}}}}catch(e){{{{console.error('Copy error:',e);}}}}}}}})()"

        # Cell styles - seamless colored cells, subtle borders on others
        cell_style = "padding: 8px; text-align: center;"

        # Build single HTML for all data cells (reduces 6 widgets to 1)
        chore_width_num = int(chore_column_width.replace('px', ''))
        data_cells_html = f"""<div style='display: flex; align-items: center;'>
            <div style='{cell_style} text-align: left; font-weight: 500; cursor: pointer; width: {chore_width_num}px;' title='Click to copy: {html_escaped_name}' onclick="{onclick_handler}">{truncated_name}</div>
            <div style='{cell_style} background-color: {days_color}; color: {get_text_color(days_color)}; width: 120px;'>{self.format_number(days_until_due)}</div>
            <div style='{cell_style} background-color: {freq_color}; color: {get_text_color(freq_color)}; width: 120px;'>{self.format_number(frequency)}</div>
            <div style='{cell_style} background-color: {relative_color}; color: {get_text_color(relative_color)}; width: 130px;'>{self.format_number(relative_ratio)}</div>
            <div style='{cell_style} width: 95px;'>{format_date_consistent(last_logged_dt)}</div>
            <div style='{cell_style} width: 95px;'>{format_date_consistent(next_due_dt)}</div>
        </div>"""

        data_cells = widgets.HTML(value=data_cells_html)

        # Buttons - fixed size to match header and row height
        btn_layout = widgets.Layout(width="50px", height="42px")
        log_button = widgets.Button(
            description="✓",
            layout=btn_layout,
            button_style='',
            style={'button_color': '#f8f8f8'}
        )
        def on_log_clicked(b):
            import sys
            from io import StringIO
            
            # Capture print output from log_chore
            old_stdout = sys.stdout
            sys.stdout = captured_output = StringIO()
            
            try:
                next_due, logged_chores = self.manager.log_chore(name)
            finally:
                # Restore stdout
                sys.stdout = old_stdout
            
            # Force a complete refresh by clearing any cached data and getting fresh data
            self.force_complete_refresh()
            
            # Display captured output in notification area AFTER refresh
            output_text = captured_output.getvalue()
            if output_text:
                with self.notification_area:
                    clear_output()
                    # Display as pre-formatted text to preserve formatting
                    display(widgets.HTML(
                        value=f"<pre style='margin: 0; padding: 10px; background-color: #f5f5f5; "
                              f"border-left: 4px solid #1890ff; font-family: monospace; white-space: pre-wrap;'>"
                              f"{output_text}</pre>"
                    ))
        log_button.on_click(on_log_clicked)

        # Use pre-loaded has_children value if available
        has_child_chores = has_children if has_children is not None else self.has_children(name)
        
        placeholder_layout = widgets.Layout(width="50px", height="38px")

        if has_child_chores:
            children_button = widgets.Button(
                description="+",
                layout=btn_layout,
                button_style='',
                style={'button_color': '#f0f0f0'}
            )
            def on_children_clicked(b):
                visited = set()
                child_details = self.get_children_details(name, visited)
                if child_details and on_expand:
                    on_expand(name, child_details)
                else:
                    with self.notification_area:
                        clear_output()
                        display(widgets.HTML(
                            value=f"<div style='padding: 10px; color: #666;'>{name} has no children to display.</div>"
                        ))
            children_button.on_click(on_children_clicked)

            leaf_button = widgets.Button(
                description="🍃",
                tooltip="Show leaf chores",
                layout=btn_layout,
                button_style='',
                style={'button_color': '#f0f0f0'}
            )

            def on_leaf_clicked(b):
                leaf_chores = self.manager.get_leaf_chores(name)
                if leaf_chores and on_expand:
                    leaf_details = []
                    for chore in leaf_chores:
                        detail = {
                            "name": chore['name'],
                            "days_until_due": chore['days_until_due'],
                            "frequency_in_days": chore['frequency_in_days'],
                            "time_since_last_log": chore['time_since_last_log']
                        }
                        leaf_details.append(detail)
                    on_expand(f"Leaf: {name}", leaf_details)
                else:
                    with self.notification_area:
                        clear_output()
                        display(widgets.HTML(
                            value=f"<div style='padding: 10px; color: #666;'>{name} has no leaf chores to display.</div>"
                        ))
            leaf_button.on_click(on_leaf_clicked)
        else:
            # Empty placeholders matching button size
            children_button = widgets.HTML(value="", layout=placeholder_layout)
            leaf_button = widgets.HTML(value="", layout=placeholder_layout)

        # Use pre-loaded note count if available
        note_count_value = note_count if note_count is not None else self.get_note_count(name)

        notes_button = widgets.Button(
            description="📝" if note_count_value > 0 else "📄",
            tooltip=(f"{note_count_value} note{'s' if note_count_value > 1 else ''}"
                    if note_count_value > 0 else "No notes yet"),
            layout=btn_layout,
            button_style='',
            style={'button_color': '#f8f8f8' if note_count_value > 0 else '#f0f0f0'}
        )
        def on_notes_clicked(b):
            with self.notification_area:
                clear_output()
                dialog_container = widgets.HBox(layout=widgets.Layout(width='100%', margin='0'))
                notes_dialog, close_button = self.create_notes_dialog(name)
                def on_close(b):
                    dialog_container.layout.display = 'none'
                close_button.on_click(on_close)
                dialog_container.children = [notes_dialog]
                display(dialog_container)
        notes_button.on_click(on_notes_clicked)

        # Assemble the row (data_cells combines all 6 data columns into 1 widget)
        row = widgets.HBox(
            [data_cells, log_button, notes_button, children_button, leaf_button],
            layout=widgets.Layout(margin="0", padding="0", border="none", align_items="center", gap="0px")
        )
        return row

    def filter_chores(self, chores, name_filter=None, parent_filter=None, include_children=True):
        """
        Filters chores based on name, parent name, and parent status.
        
        Parameters:
            chores: List of chore details to filter
            name_filter: String to filter chore names (case-insensitive)
            parent_filter: String to filter parent chore names (case-insensitive)
            include_children: If False, only return chores without parents
            
        Returns:
            Filtered list of chore details
        """
        if not name_filter and not parent_filter and include_children:
            return chores
        
        filtered_chores = chores
        
        # Filter by chore name if provided
        if name_filter:
            name_filter = name_filter.lower()
            filtered_chores = [c for c in filtered_chores if name_filter in c["name"].lower()]
        
        # Filter by parent name if provided
        if parent_filter and self.manager:
            parent_filter = parent_filter.lower()
            # Get all parent-child relationships
            cur = self.manager.connection.cursor()
            cur.execute("SELECT chore_name, parent_chore FROM parent_chores")
            parent_relations = cur.fetchall()
            
            # Create a dictionary mapping chore names to their parents
            chore_to_parents = {}
            for relation in parent_relations:
                child = relation["chore_name"]
                parent = relation["parent_chore"]
                if child not in chore_to_parents:
                    chore_to_parents[child] = []
                chore_to_parents[child].append(parent)
            
            # Filter chores based on parent names
            filtered_by_parent = []
            for chore in filtered_chores:
                name = chore["name"]
                if name in chore_to_parents:
                    parents = chore_to_parents[name]
                    if any(parent_filter in parent.lower() for parent in parents):
                        filtered_by_parent.append(chore)
            
            filtered_chores = filtered_by_parent
        
        # Filter to only show top-level chores (those without parents) if include_children is False
        if not include_children and self.manager:
            # Get all chores that are children in parent-child relationships
            cur = self.manager.connection.cursor()
            cur.execute("SELECT DISTINCT chore_name FROM parent_chores")
            child_rows = cur.fetchall()
            child_chores = {row["chore_name"] for row in child_rows}
            
            # Filter out chores that are children
            filtered_chores = [c for c in filtered_chores if c["name"] not in child_chores]
        
        return filtered_chores

    def create_chore_table(self, chore_details=None, page=0, page_size=None, on_expand=None, name_filter=None, parent_filter=None, include_children=True):
        """
        Returns a VBox widget representing the table of chores with optimized data loading.
        
        Parameters:
            chore_details: Optional list of chore details to display
            page: The page number (0-indexed)
            page_size: The number of items per page
            on_expand: Callback function for when a chore is expanded
            name_filter: String to filter chore names (case-insensitive)
            parent_filter: String to filter parent chore names (case-insensitive)
            include_children: If False, only show top-level chores (those without parents)
        """
        start_time = datetime.now()
        
        if page_size is None:
            page_size = self.page_size
            
        if chore_details is None:
            chore_details = self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due")
        
        if name_filter is None:
            name_filter = self.name_filter
            
        if parent_filter is None:
            parent_filter = self.parent_filter
            
        if include_children is None:
            include_children = self.include_children
        
        # Apply filters if provided
        filtered_chores = self.filter_chores(chore_details, name_filter, parent_filter, include_children)
        
        # Calculate total pages
        total_pages = (len(filtered_chores) + page_size - 1) // page_size
        
        # Ensure page is within bounds
        page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
        
        # Get the slice of chores for the current page
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(filtered_chores))
        page_chores = filtered_chores[start_idx:end_idx]
        
        # Extract chore names for batch loading
        chore_names = [chore["name"] for chore in page_chores]
        
        # Calculate optimal chore column width based on longest truncated name on this page
        if chore_names:
            # Apply the same truncation logic that will be used for display
            truncated_names = [self.truncate_text(name, max_length=50) for name in chore_names]
            # Find the longest truncated name and calculate width
            longest_truncated_name = max(truncated_names, key=len)
            # More precise width calculation: ~6.5px per character + minimal padding
            calculated_width = min(max(len(longest_truncated_name) * 6.5 + 25, 250), 500)
            chore_column_width = f"{int(calculated_width)}px"
        else:
            chore_column_width = "300px"  # Default width if no chores
        
        # Batch load additional data needed for display
        has_children_map, note_counts_map, logs_map = self.manager.batch_load_chore_data(chore_names)
        
        # Use the global STATS_RANGES instead of redefining them here
        stats_ranges = STATS_RANGES
        
        # Header - structure matches row: data columns HTML + button column HTMLs
        header_cell_style = "padding: 8px; text-align: center; font-weight: 600; color: #555; border-bottom: 2px solid #ddd;"
        chore_width_num = int(chore_column_width.replace('px', ''))

        # Data columns header (matches data_cells structure)
        header_data_html = f"""<div style='display: flex; align-items: center;'>
            <div style='{header_cell_style} text-align: left; width: {chore_width_num}px;'>Chore</div>
            <div style='{header_cell_style} width: 120px;'>Days Until Due</div>
            <div style='{header_cell_style} width: 120px;'>Frequency</div>
            <div style='{header_cell_style} width: 130px;'>Relative Urgency</div>
            <div style='{header_cell_style} width: 95px;'>Last Logged</div>
            <div style='{header_cell_style} width: 95px;'>Next Due</div>
        </div>"""

        # Button column headers (separate widgets to match row button widgets)
        btn_header_layout = widgets.Layout(width="50px")
        btn_header_cell = "padding: 8px 4px; text-align: center; font-weight: 600; color: #555; border-bottom: 2px solid #ddd;"
        header = widgets.HBox([
            widgets.HTML(value=header_data_html),
            widgets.HTML(value=f"<div style='{btn_header_cell}'>Log</div>", layout=btn_header_layout),
            widgets.HTML(value=f"<div style='{btn_header_cell}'>Notes</div>", layout=btn_header_layout),
            widgets.HTML(value=f"<div style='{btn_header_cell}'>Expand</div>", layout=btn_header_layout),
            widgets.HTML(value=f"<div style='{btn_header_cell}'>Leaf</div>", layout=btn_header_layout),
        ], layout=widgets.Layout(margin="0", padding="0", gap="0px"))
        
        rows = [header]
        for i, chore_detail in enumerate(page_chores):
            row = self.create_chore_row(
                chore_detail, 
                i, 
                stats_ranges, 
                on_expand=on_expand if on_expand else self.handle_expansion,
                has_children=has_children_map.get(chore_detail["name"], False),
                note_count=note_counts_map.get(chore_detail["name"], 0),
                log_data=logs_map.get(chore_detail["name"], None),
                chore_column_width=chore_column_width
            )
            rows.append(row)
        
        prev_button = widgets.Button(
            description="Prev",
            disabled=page == 0,
            layout=widgets.Layout(width='70px'),
            style={'button_color': '#f0f0f0'}
        )
        
        # Create an editable field for the current page
        page_input = widgets.IntText(
            value=page + 1,  # Display is 1-indexed
            min=1,
            max=total_pages if total_pages > 0 else 1,
            layout=widgets.Layout(width='50px'),
            style={'description_width': '0px'}
        )
        
        # Display for total pages
        total_pages_label = widgets.HTML(
            value=f"<div style='padding: 5px 5px 0 5px; color: #666;'>of {total_pages} pages</div>"
        )
        
        next_button = widgets.Button(
            description="Next",
            disabled=page >= total_pages - 1 or total_pages == 0,
            layout=widgets.Layout(width='70px'),
            style={'button_color': '#f0f0f0'}
        )
        
        def on_prev_clicked(b):
            if page > 0:
                self.update_display(page=page-1)
        
        def on_next_clicked(b):
            if page < total_pages - 1:
                self.update_display(page=page+1)
        
        def on_page_change(change):
            if change['name'] == 'value':
                new_page = change['new'] - 1  # Convert from 1-indexed to 0-indexed
                if 0 <= new_page < total_pages:
                    self.update_display(page=new_page)
                else:
                    # Reset to valid value if out of range
                    page_input.value = page + 1
        
        prev_button.on_click(on_prev_clicked)
        next_button.on_click(on_next_clicked)
        page_input.observe(on_page_change)
        
        # Calculate the width of the table data area (sum of column widths)
        # Note: Auto-sizing date columns use 100px average (85-120px range), chore column uses calculated width
        chore_width_num = int(chore_column_width.replace('px', ''))
        table_width = chore_width_num + 120 + 120 + 130 + 100 + 100 + 60 + 60 + 60 + 60 + 60  # Calculated chore width + other columns
        
        # Create a container with the same width as the table data area
        pagination_container = widgets.Box(
            layout=widgets.Layout(
                width=f'{table_width}px',
                margin='0 auto'  # This centers the box itself
            )
        )
        
        # Inside the container, create the centered controls
        pagination_controls = widgets.HBox(
            [prev_button, page_input, total_pages_label, next_button],
            layout=widgets.Layout(
                justify_content='center',
                align_items='center',
                width='100%',
                margin='10px 0'
            )
        )
        
        pagination_container.children = [pagination_controls]
        
        # Add pagination info (also centered)
        pagination_info = widgets.HTML(
            value=f"<div style='text-align: center; padding: 5px;'>Showing {start_idx + 1}-{end_idx} of {len(filtered_chores)} chores</div>"
        )
        
        # Log the time it took to generate the table
        end_time = datetime.now()
        generation_time = (end_time - start_time).total_seconds()
        time_info = widgets.HTML(
            value=f"<div style='text-align: center; padding: 5px; color: #888; font-size: 0.8em;'>Table generated in {generation_time:.2f} seconds</div>"
        )
        
        # Create a table with no background - pure Tufte
        table = widgets.VBox(
            rows + [pagination_controls, pagination_info, time_info],
            layout=widgets.Layout(
                margin="10px 0",
                padding="0",
                border="none",
                background_color="transparent"
            )
        )
        return table, page, total_pages

    def create_breadcrumb_widget(self):
        """Creates a breadcrumb navigation widget based on the current path."""
        if not self.breadcrumb_path:
            return None
        
        def truncate_breadcrumb_text(text, max_length=50):
            """Truncate breadcrumb text if too long"""
            if len(text) > max_length:
                return text[:max_length-3] + "..."
            return text
        
        # Create buttons for each item in the path
        breadcrumb_items = []
        for i, (name, _) in enumerate(self.breadcrumb_path):
            # Truncate long breadcrumb names
            display_name = truncate_breadcrumb_text(name)
            
            # Create a button for this breadcrumb item with width limits
            btn_kwargs = {
                'description': display_name,
                'layout': widgets.Layout(height='30px', max_width='350px', width='auto'),
                'button_style': '',
                'style': {'button_color': '#f0f0f0' if i < len(self.breadcrumb_path) - 1 else '#e0e0e0'}
            }
            
            # Only add tooltip if the name was truncated
            if len(name) > 50:
                btn_kwargs['tooltip'] = name
                
            btn = widgets.Button(**btn_kwargs)
            
            # Define what happens when this breadcrumb is clicked
            def on_breadcrumb_click(b, idx=i):
                # Navigate to this level by truncating the path
                self.navigate_to_level(idx)
            
            btn.on_click(on_breadcrumb_click)
            breadcrumb_items.append(btn)
            
            # Add an arrow between items (except for the last one)
            if i < len(self.breadcrumb_path) - 1:
                arrow = widgets.HTML(
                    value="<div style='padding: 0 8px; font-size: 16px; color: #666;'>→</div>",
                    layout=widgets.Layout(width='auto')
                )
                breadcrumb_items.append(arrow)
        
        # Create a horizontal box for the breadcrumb with scroll prevention
        return widgets.HBox(
            breadcrumb_items,
            layout=widgets.Layout(
                width='100%',
                overflow_x='hidden',  # Prevent horizontal scrolling
                flex_wrap='wrap',     # Allow wrapping if needed
                align_items='center'
            )
        )

    def create_search_widget(self):
        """Creates a search widget with name, parent filters, and a Children checkbox."""
        # Create text inputs for name and parent filters
        name_input = widgets.Text(
            value=self.name_filter,
            placeholder='Filter by chore name',
            description='Name:',
            layout=widgets.Layout(width='240px')
        )
        
        parent_input = widgets.Text(
            value=self.parent_filter,
            placeholder='Filter by parent name',
            description='Parent:',
            layout=widgets.Layout(width='240px')
        )
        
        # Create checkbox for children - checked by default
        # Increase width to ensure label is visible
        children_checkbox = widgets.Checkbox(
            value=self.include_children,
            description='Children',
            indent=False,
            layout=widgets.Layout(width='120px')
        )
        
        # Create buttons
        search_button = widgets.Button(
            description='Search',
            button_style='primary',
            layout=widgets.Layout(width='80px')
        )
        
        clear_button = widgets.Button(
            description='Clear',
            layout=widgets.Layout(width='80px')
        )
        
        def on_search_clicked(b):
            self.name_filter = name_input.value
            self.parent_filter = parent_input.value
            self.include_children = children_checkbox.value
            self.current_page = 0  # Reset to first page when searching
            self.update_display()
        
        def on_clear_clicked(b):
            self.name_filter = ""
            self.parent_filter = ""
            self.include_children = True  # Reset to default (show children)
            name_input.value = ""
            parent_input.value = ""
            children_checkbox.value = True
            self.current_page = 0  # Reset to first page when clearing
            self.update_display()
        
        # Update filters when Enter is pressed in either input
        def on_name_submit(sender):
            on_search_clicked(None)
        
        def on_parent_submit(sender):
            on_search_clicked(None)
        
        # Update filters when checkbox changes
        def on_checkbox_change(change):
            if change['name'] == 'value':
                on_search_clicked(None)
        
        name_input.on_submit(on_name_submit)
        parent_input.on_submit(on_parent_submit)
        children_checkbox.observe(on_checkbox_change, names=['value'])
        search_button.on_click(on_search_clicked)
        clear_button.on_click(on_clear_clicked)
        
        # Create checkbox container with explicit label
        checkbox_container = widgets.HBox(
            [children_checkbox],
            layout=widgets.Layout(
                width='100px',
                margin='0px 10px'
            )
        )
        
        # Assemble the search widget into a single row with better spacing
        search_widget = widgets.HBox(
            [name_input, parent_input, checkbox_container, search_button, clear_button],
            layout=widgets.Layout(
                justify_content='flex-start',
                align_items='center',
                width='100%',
                margin='10px 0'
            )
        )
        
        return search_widget

    def navigate_to_level(self, level):
        """Navigate to a specific level in the breadcrumb path."""
        if level < 0 or level >= len(self.breadcrumb_path):
            return
        
        # Truncate the path to the selected level
        self.breadcrumb_path = self.breadcrumb_path[:level+1]
        
        # If we're navigating back to the main view, refresh with latest data
        if len(self.breadcrumb_path) == 1 and self.breadcrumb_path[0][0] == "Main":
            fresh_main_data = self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due")
            self.breadcrumb_path[0] = ("Main", fresh_main_data)
        
        # Reset to first page and clear filters when navigating
        self.current_page = 0
        self.name_filter = ""
        self.parent_filter = ""
        
        # Update the display
        self.update_display()

    def update_display(self, page=None):
        """Update the displayed table and breadcrumb based on the current path."""
        # Update page if provided
        if page is not None:
            self.current_page = page
        
        # Get the current level's details
        _, current_details = self.breadcrumb_path[-1]
        
        # Update the search widget
        with self.search_container:
            clear_output()
            search_widget = self.create_search_widget()
            display(search_widget)
        
        # Update the table
        with self.table_container:
            clear_output()
            table, updated_page, total_pages = self.create_chore_table(
                chore_details=current_details, 
                page=self.current_page,
                page_size=self.page_size,
                on_expand=self.handle_expansion,
                name_filter=self.name_filter,
                parent_filter=self.parent_filter,
                include_children=self.include_children
            )
            self.current_page = updated_page  # Update in case page was adjusted
            display(table)
        
        # Update the breadcrumb
        with self.breadcrumb_container:
            clear_output()
            breadcrumb = self.create_breadcrumb_widget()
            if breadcrumb:
                display(breadcrumb)
        
        # Don't clear notifications - let them persist (especially log messages)

    def handle_expansion(self, parent_name, child_details):
        """Handle the expansion of a chore to show its children."""
        # Add this expansion to the path
        self.breadcrumb_path.append((parent_name, child_details))
        
        # Reset to first page and clear filters when expanding
        self.current_page = 0
        self.name_filter = ""
        self.parent_filter = ""
        
        # Update the display
        self.update_display()

    def get_notes(self, chore_name):
        """Retrieve all notes for a given chore with creation dates."""
        cur = self.manager.connection.cursor()
        cur.execute("""
            SELECT id, note, created_at 
            FROM notes 
            WHERE chore_name = ?
            ORDER BY created_at DESC
        """, (chore_name,))
        return cur.fetchall()

    def add_note(self, chore_name, note_text):
        """Add a new note for the specified chore."""
        cur = self.manager.connection.cursor()
        cur.execute("""
            INSERT INTO notes (chore_name, note, created_at)
            VALUES (?, ?, ?)
        """, (chore_name, note_text, datetime.now().isoformat()))
        self.manager.connection.commit()

    def get_note_count(self, chore_name):
        """Get the number of notes for a chore."""
        cur = self.manager.connection.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM notes WHERE chore_name = ?", (chore_name,))
        result = cur.fetchone()
        return result["cnt"] if result else 0

    def create_notes_dialog(self, chore_name):
        """Create a dialog for viewing and adding notes for a chore."""
        # Container for the entire notes dialog
        notes_container = widgets.VBox(layout=widgets.Layout(
            width='400px',
            padding='10px',
            margin='0 0 0 20px',  # Add margin to separate from table
            border='1px solid #ddd',
            border_radius='4px',
            background_color='white'
        ))
        
        # Header with chore name
        header = widgets.HTML(
            value=f"<h3 style='margin: 0 0 10px 0;'>Notes for: {chore_name}</h3>"
        )
        
        # Existing notes area
        notes_area = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            max_height='300px',
            overflow_y='auto'
        ))
        
        def format_note(note):
            """Format a single note with creation date."""
            try:
                created_at = datetime.fromisoformat(note['created_at']).strftime('%Y-%m-%d %H:%M')
                return widgets.HTML(
                    value=f"""
                    <div style='
                        margin-bottom: 10px;
                        padding: 8px;
                        background: #f8f8f8;
                        border-left: 3px solid #ddd;
                        border-radius: 3px;
                    '>
                        <div style='color: #666; font-size: 0.8em; margin-bottom: 4px;'>
                            {created_at}
                        </div>
                        <div style='white-space: pre-wrap;'>{note['note']}</div>
                    </div>
                    """
                )
            except (KeyError, ValueError, TypeError):
                # Fallback if no creation date is available
                return widgets.HTML(
                    value=f"""
                    <div style='
                        margin-bottom: 10px;
                        padding: 8px;
                        background: #f8f8f8;
                        border-left: 3px solid #ddd;
                        border-radius: 3px;
                        white-space: pre-wrap;
                    '>{note['note']}</div>
                    """
                )
        
        def update_notes_display():
            """Refresh the notes display."""
            notes = self.get_notes(chore_name)
            notes_area.children = tuple(format_note(note) for note in notes)
        
        # New note input without timestamp suggestion
        new_note_input = widgets.Textarea(
            placeholder='Enter a new note...',
            layout=widgets.Layout(width='100%', height='100px')
        )
        
        # Add note & log button (primary action)
        add_and_log_button = widgets.Button(
            description='Add Note & Log',
            button_style='primary',
            layout=widgets.Layout(width='130px')
        )
        
        # Add note only button (secondary)
        add_button = widgets.Button(
            description='Add Note',
            button_style='',
            layout=widgets.Layout(width='100px')
        )
        
        # Close button
        close_button = widgets.Button(
            description='Close',
            layout=widgets.Layout(width='100px')
        )
        
        def on_add_clicked(b):
            if new_note_input.value.strip():
                self.add_note(chore_name, new_note_input.value.strip())
                new_note_input.value = ''  # Clear input
                update_notes_display()  # Refresh notes display
        
        def on_add_and_log_clicked(b):
            import sys
            from io import StringIO
            
            if new_note_input.value.strip():
                # Add the note first
                self.add_note(chore_name, new_note_input.value.strip())
                new_note_input.value = ''  # Clear input
                update_notes_display()  # Refresh notes display
            
            # Capture print output from log_chore
            old_stdout = sys.stdout
            sys.stdout = captured_output = StringIO()
            
            try:
                # Log the chore (same as clicking the log button) - always happens
                next_due, logged_chores = self.manager.log_chore(chore_name)
            finally:
                # Restore stdout
                sys.stdout = old_stdout
            
            # Force complete refresh since logging changes due dates
            self.force_complete_refresh()
            
            # Display captured output in notification area AFTER refresh
            output_text = captured_output.getvalue()
            if output_text:
                with self.notification_area:
                    clear_output()
                    # Display as pre-formatted text to preserve formatting
                    display(widgets.HTML(
                        value=f"<pre style='margin: 0; padding: 10px; background-color: #f5f5f5; "
                              f"border-left: 4px solid #1890ff; font-family: monospace; white-space: pre-wrap;'>"
                              f"{output_text}</pre>"
                    ))
        
        add_button.on_click(on_add_clicked)
        add_and_log_button.on_click(on_add_and_log_clicked)

        # Button container
        button_container = widgets.HBox(
            [add_and_log_button, add_button, close_button],
            layout=widgets.Layout(
                justify_content='flex-end',
                margin='10px 0 0 0'
            )
        )
        
        # Assemble the dialog
        notes_container.children = [
            header,
            notes_area,
            new_note_input,
            button_container
        ]
        
        # Initial notes display
        update_notes_display()
        
        return notes_container, close_button

    def refresh_table(self):
        """Refresh the main table while preserving current state."""
        # Save the current state
        current_breadcrumb_path = self.breadcrumb_path.copy()
        saved_page = self.current_page
        saved_name_filter = self.name_filter
        saved_parent_filter = self.parent_filter
        saved_include_children = self.include_children
        
        # Instead of resetting everything, just update the chore data
        if current_breadcrumb_path:
            # Update the current level's data (the last item in the breadcrumb path)
            level_name, _ = current_breadcrumb_path[-1]
            
            # If we're on the main view, get fresh data
            if level_name == "Main":
                updated_data = self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due")
            # If we're looking at children of a parent
            elif level_name.startswith("Leaf: "):
                parent_name = level_name[6:]  # Remove "Leaf: " prefix
                leaf_chores = self.manager.get_leaf_chores(parent_name)
                updated_data = []
                for chore in leaf_chores:
                    detail = {
                        "name": chore['name'],
                        "days_until_due": chore['days_until_due'],
                        "frequency_in_days": chore['frequency_in_days'],
                        "time_since_last_log": chore['time_since_last_log']
                    }
                    updated_data.append(detail)
            # For regular parent view
            else:
                visited = set()
                updated_data = self.get_children_details(level_name, visited)
            
            # Update the current view with fresh data
            current_breadcrumb_path[-1] = (level_name, updated_data)
            self.breadcrumb_path = current_breadcrumb_path
            
            # Restore state
            self.current_page = saved_page
            self.name_filter = saved_name_filter
            self.parent_filter = saved_parent_filter
            self.include_children = saved_include_children
            
            # Check if the current page is still valid (the number of items might have changed)
            filtered_data = self.filter_chores(updated_data, self.name_filter, self.parent_filter, self.include_children)
            total_pages = (len(filtered_data) + self.page_size - 1) // self.page_size
            if self.current_page >= total_pages and total_pages > 0:
                self.current_page = total_pages - 1
        
        # Update the display with the preserved state
        self.update_display()

    def force_complete_refresh(self):
        """Force a complete refresh of all data, ensuring fresh database queries."""
        # Save the current state
        saved_page = self.current_page
        saved_name_filter = self.name_filter
        saved_parent_filter = self.parent_filter
        saved_include_children = self.include_children
        
        # Get completely fresh data from the database
        fresh_main_data = self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due")
        
        # Update the breadcrumb path with fresh data
        if self.breadcrumb_path:
            level_name, _ = self.breadcrumb_path[-1]
            
            if level_name == "Main":
                # Update main view with fresh data
                self.breadcrumb_path[-1] = (level_name, fresh_main_data)
                # Also update the main view in the breadcrumb path if it exists at position 0
                if len(self.breadcrumb_path) > 1 and self.breadcrumb_path[0][0] == "Main":
                    self.breadcrumb_path[0] = ("Main", fresh_main_data)
            elif level_name.startswith("Leaf: "):
                # Refresh leaf chores
                parent_name = level_name[6:]  # Remove "Leaf: " prefix
                leaf_chores = self.manager.get_leaf_chores(parent_name)
                updated_data = []
                for chore in leaf_chores:
                    detail = {
                        "name": chore['name'],
                        "days_until_due": chore['days_until_due'],
                        "frequency_in_days": chore['frequency_in_days'],
                        "time_since_last_log": chore['time_since_last_log']
                    }
                    updated_data.append(detail)
                self.breadcrumb_path[-1] = (level_name, updated_data)
                # Update main view data as well since user might navigate back
                if self.breadcrumb_path[0][0] == "Main":
                    self.breadcrumb_path[0] = ("Main", fresh_main_data)
            else:
                # Refresh children details
                visited = set()
                fresh_children_data = self.get_children_details(level_name, visited)
                self.breadcrumb_path[-1] = (level_name, fresh_children_data)
                # Update main view data as well since user might navigate back
                if self.breadcrumb_path[0][0] == "Main":
                    self.breadcrumb_path[0] = ("Main", fresh_main_data)
        else:
            # If no breadcrumb path, initialize with main view
            self.breadcrumb_path = [("Main", fresh_main_data)]
        
        # Restore state
        self.current_page = saved_page
        self.name_filter = saved_name_filter
        self.parent_filter = saved_parent_filter
        self.include_children = saved_include_children
        
        # Check if the current page is still valid
        if self.breadcrumb_path:
            _, current_data = self.breadcrumb_path[-1]
            filtered_data = self.filter_chores(current_data, self.name_filter, self.parent_filter, self.include_children)
            total_pages = (len(filtered_data) + self.page_size - 1) // self.page_size
            if self.current_page >= total_pages and total_pages > 0:
                self.current_page = total_pages - 1
        
        # Update the display
        self.update_display()

    def display(self):
        """
        Display the interactive chore table.
        
        Returns:
            A function that can be called to refresh the table
        """
        # Initialize the path with the main view
        self.breadcrumb_path = [("Main", self.manager.get_sorted_due_chores(leaf_only=False, sort_by="days_until_due"))]
        self.current_page = 0
        self.name_filter = ""
        self.parent_filter = ""
        self.include_children = True  # Default to showing children
        
        # Minimal CSS - avoid breaking widget rendering
        display(HTML("""
        <style>
        .widget-textarea textarea {
            resize: vertical !important;
        }
        </style>
        """))
        
        # Update the display
        self.update_display()
        
        # Display the main layout
        display(self.main_layout)
        
        # Return the refresh function for external use
        return self.refresh_table
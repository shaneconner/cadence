import ipywidgets as widgets
from IPython.display import display, clear_output
from datetime import datetime, timedelta

class CadenceEditor:
    def __init__(self, manager):
        """
        Initialize the CadenceEditor with a CadenceManager instance.

        Parameters:
            manager: CadenceManager instance
        """
        self.manager = manager
        self.current_chore = None
        self.logs_limit = 5  # Initial limit for logs display
        
        # Main container with fixed width
        self.main_container = widgets.VBox(layout=widgets.Layout(
            width='800px',
            padding='20px',
            overflow='visible'
        ))
        
        # Create the chore selector
        self.create_chore_selector()
        
        # Create containers for different sections with overflow visible
        self.info_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        self.parents_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        self.children_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        self.notes_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        self.logs_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        self.urls_container = widgets.VBox(layout=widgets.Layout(
            margin='10px 0',
            overflow='visible',
            width='100%'
        ))
        
        # Status message area
        self.status_area = widgets.Output(layout=widgets.Layout(
            margin='10px 0',
            padding='10px',
            border='1px solid #ddd',
            max_height='100px',
            overflow_y='auto',
            display='none'
        ))
        
        # Initialize the layout
        self.update_layout()
    
    def create_chore_selector(self):
        """Create the dropdown to select a chore to edit."""
        # Get all chore names
        cur = self.manager.connection.cursor()
        cur.execute("SELECT name FROM chores ORDER BY name")
        chores = [row['name'] for row in cur.fetchall()]
        
        # Create the dropdown with a blank option
        self.chore_selector = widgets.Dropdown(
            options=[('', None)] + [(name, name) for name in chores],
            value=None,
            description='Chore:',
            layout=widgets.Layout(width='50%'),
            style={'description_width': 'initial'}
        )
        
        # Add observer for value changes
        self.chore_selector.observe(self.on_chore_selected, names='value')
    
    def get_chore_details(self, chore_name):
        """Get all details for a specific chore."""
        cur = self.manager.connection.cursor()
        
        # Check if active column exists in chores table
        has_active_column = False
        cur.execute("PRAGMA table_info(chores)")
        columns = cur.fetchall()
        for col in columns:
            if col['name'] == 'active':
                has_active_column = True
                break
        
        # Add active column if it doesn't exist
        if not has_active_column:
            try:
                cur.execute("ALTER TABLE chores ADD COLUMN active INTEGER DEFAULT 1")
                self.manager.connection.commit()
            except:
                pass
        
        # Check if adjust_frequency column exists
        has_adjust_frequency_column = False
        for col in columns:
            if col['name'] == 'adjust_frequency':
                has_adjust_frequency_column = True
                break
        
        # Get basic chore info
        if has_active_column and has_adjust_frequency_column:
            cur.execute("""
                SELECT name, frequency_in_days, created_at, description, active, adjust_frequency 
                FROM chores 
                WHERE name = ?
            """, (chore_name,))
        elif has_active_column:
            cur.execute("""
                SELECT name, frequency_in_days, created_at, description, active 
                FROM chores 
                WHERE name = ?
            """, (chore_name,))
        else:
            cur.execute("""
                SELECT name, frequency_in_days, created_at, description
                FROM chores 
                WHERE name = ?
            """, (chore_name,))
        
        chore_info = cur.fetchone()
        
        # Convert SQLite Row to dictionary
        chore_info_dict = dict(zip([column[0] for column in cur.description], chore_info))
        
        # Add active field if it doesn't exist
        if 'active' not in chore_info_dict:
            chore_info_dict['active'] = 1
        
        # Add adjust_frequency field if it doesn't exist
        if 'adjust_frequency' not in chore_info_dict:
            chore_info_dict['adjust_frequency'] = 1
        
        # Get parent chores
        cur.execute("""
            SELECT parent_chore 
            FROM parent_chores 
            WHERE chore_name = ?
        """, (chore_name,))
        parents = [row['parent_chore'] for row in cur.fetchall()]
        
        # Get child chores
        cur.execute("""
            SELECT chore_name 
            FROM parent_chores 
            WHERE parent_chore = ?
        """, (chore_name,))
        children = [row['chore_name'] for row in cur.fetchall()]
        
        # Get notes
        cur.execute("""
            SELECT id, note, created_at 
            FROM notes 
            WHERE chore_name = ?
            ORDER BY created_at DESC
        """, (chore_name,))
        notes_rows = cur.fetchall()
        notes = [dict(zip([column[0] for column in cur.description], row)) for row in notes_rows]
        
        # Check if is_genuine column exists
        has_is_genuine = False
        cur.execute("PRAGMA table_info(logs)")
        columns = cur.fetchall()
        for col in columns:
            if col['name'] == 'is_genuine':
                has_is_genuine = True
                break

        # Add is_genuine column if it doesn't exist
        if not has_is_genuine:
            try:
                cur.execute("ALTER TABLE logs ADD COLUMN is_genuine INTEGER DEFAULT 1")
                self.manager.connection.commit()
            except:
                pass

        # Get logs
        if has_is_genuine:
            cur.execute("""
                SELECT id, logged_at, is_genuine 
                FROM logs 
                WHERE chore_name = ?
                ORDER BY logged_at DESC
                LIMIT ?
            """, (chore_name, self.logs_limit))
        else:
            cur.execute("""
                SELECT id, logged_at 
                FROM logs 
                WHERE chore_name = ?
                ORDER BY logged_at DESC
                LIMIT ?
            """, (chore_name, self.logs_limit))

        logs_rows = cur.fetchall()
        logs = [dict(zip([column[0] for column in cur.description], row)) for row in logs_rows]
        
        # Get total count of logs
        cur.execute("SELECT COUNT(*) as count FROM logs WHERE chore_name = ?", (chore_name,))
        total_logs = cur.fetchone()['count']

        # Get URLs
        cur.execute("""
            SELECT id, url 
            FROM urls 
            WHERE chore_name = ?
        """, (chore_name,))
        urls_rows = cur.fetchall()
        urls = [dict(zip([column[0] for column in cur.description], row)) for row in urls_rows]
        
        return {
            'info': chore_info_dict,
            'parents': parents,
            'children': children,
            'notes': notes,
            'logs': logs,
            'urls': urls,
            'total_logs': total_logs  # Add total logs count
        }
    
    def create_info_section(self, chore_details):
        """Create the basic info editing section."""
        info = chore_details['info']
        
        # Name editor
        name_input = widgets.Text(
            value=self.current_chore,
            description='Name:',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='750px')
        )
        
        # Frequency editor
        frequency = widgets.FloatText(
            value=info['frequency_in_days'],
            description='Frequency (days):',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='300px')
        )
        
        # Description editor with fixed width
        description = widgets.Textarea(
            value=info['description'] or '',
            placeholder='Enter description...',
            description='Description:',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='750px', height='100px')
        )
        
        # Active status toggle
        active_status = widgets.Checkbox(
            value=bool(info['active']),
            description='Active',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='150px')
        )
        
        # Adjust frequency toggle
        adjust_frequency = widgets.Checkbox(
            value=bool(info.get('adjust_frequency', 1)),
            description='Adjust Frequency',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='180px')
        )
        
        # Created date display and editor
        try:
            chore_date = datetime.fromisoformat(info['created_at'])
            created_at_str = chore_date.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            chore_date = datetime.now()
            created_at_str = "Unknown date"
        
        # Date picker for editing the creation date
        date_picker = widgets.Text(
            value=chore_date.strftime('%Y-%m-%d'),
            description='Created:',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='200px')
        )
        
        # Time picker for editing the creation time
        time_picker = widgets.Text(
            value=chore_date.strftime('%H:%M'),
            description='Time:',
            style={'description_width': 'initial'},
            layout=widgets.Layout(width='150px')
        )
        
        # Create date/time editor row with overflow visible
        date_time_row = widgets.HBox([date_picker, time_picker], layout=widgets.Layout(
            margin='5px 0',
            align_items='center',
            overflow='visible'
        ))
        
        # Save button
        save_button = widgets.Button(
            description='Save Changes',
            button_style='primary',
            layout=widgets.Layout(width='150px')
        )

        def on_save(b):
            new_name = name_input.value.strip()
            old_name = self.current_chore
            
            if not new_name:
                self.show_status("Chore name cannot be empty!")
                return
            
            # Parse date and time
            try:
                date_str = date_picker.value
                time_str = time_picker.value
                datetime_str = f"{date_str}T{time_str}:00"
                # Validate by parsing
                datetime.fromisoformat(datetime_str)
            except:
                self.show_status("Invalid date or time format! Use YYYY-MM-DD and HH:MM")
                return
            
            # Prepare updates dictionary
            updates = {
                'frequency_in_days': frequency.value,
                'description': description.value,
                'active': int(active_status.value),
                'adjust_frequency': int(adjust_frequency.value),
                'created_at': datetime_str
            }
            
            # Add name to updates if it has changed
            if new_name != old_name:
                updates['name'] = new_name
            
            # Use the manager's update_chore_attributes method
            if self.manager.update_chore_attributes(old_name, updates):
                # Update the current chore name if it changed
                if new_name != old_name:
                    self.current_chore = new_name
                
                # Show success message
                self.show_status("Chore information saved successfully!")
                
                # Refresh the chore selector to reflect any name change
                self.create_chore_selector()
                self.chore_selector.value = new_name
                
                # Refresh the display
                self.refresh()
            else:
                self.show_status("Error saving changes!")
        
        save_button.on_click(on_save)
        
        # Create a row for the active status and adjust frequency
        status_row = widgets.HBox([active_status, adjust_frequency], layout=widgets.Layout(
            justify_content='flex-start',
            width='100%'
        ))
        
        return widgets.VBox([
            widgets.HTML("<h3>Basic Information</h3>"),
            name_input,
            frequency,
            description,
            status_row,
            widgets.HTML("<h4>Creation Date</h4>"),
            date_time_row,
            save_button
        ], layout=widgets.Layout(overflow='visible'))
    
    def create_parents_section(self, chore_details):
        """Create the parents editing section."""
        parents = chore_details['parents']
        
        # Create a list of parent items with remove buttons
        parent_items = []
        for parent in parents:
            parent_label = widgets.HTML(f"<div style='padding: 5px 0'>{parent}</div>")
            remove_btn = widgets.Button(
                description='Remove',
                button_style='danger',
                layout=widgets.Layout(width='80px', height='30px')
            )
            
            def on_remove(b, parent_to_remove=parent):
                cur = self.manager.connection.cursor()
                cur.execute("""
                    DELETE FROM parent_chores 
                    WHERE chore_name = ? AND parent_chore = ?
                """, (self.current_chore, parent_to_remove))
                self.manager.connection.commit()
                self.show_status(f"Removed parent: {parent_to_remove}")
                self.refresh()
            
            remove_btn.on_click(on_remove)
            parent_items.append(widgets.HBox([parent_label, remove_btn], layout=widgets.Layout(
                justify_content='space-between',
                width='100%'
            )))
        
        # Create "Add Parent" button and dropdown
        add_parent_btn = widgets.Button(
            description='Add Parent',
            button_style='primary',
            layout=widgets.Layout(width='100px')
        )
        
        # Get all possible parent chores (excluding current chore and its children)
        cur = self.manager.connection.cursor()
        cur.execute("""
            SELECT name FROM chores 
            WHERE name != ? AND name NOT IN (
                SELECT chore_name FROM parent_chores WHERE parent_chore = ?
            )
            ORDER BY name
        """, (self.current_chore, self.current_chore))
        available_parents = [row['name'] for row in cur.fetchall()]
        
        # Filter out existing parents
        available_parents = [p for p in available_parents if p not in parents]
        
        parent_dropdown = widgets.Dropdown(
            options=[('', None)] + [(p, p) for p in available_parents] if available_parents else [('No available parents', None)],
            value=None,
            description='Select:',
            layout=widgets.Layout(width='300px'),
            style={'description_width': 'initial'},
            disabled=not available_parents
        )
        
        def on_add_parent(b):
            if parent_dropdown.value:
                cur = self.manager.connection.cursor()
                cur.execute("""
                    INSERT INTO parent_chores (chore_name, parent_chore)
                    VALUES (?, ?)
                """, (self.current_chore, parent_dropdown.value))
                self.manager.connection.commit()
                self.show_status(f"Added parent: {parent_dropdown.value}")
                self.refresh()
        
        add_parent_btn.on_click(on_add_parent)
        
        # Assemble the section
        parent_list = widgets.VBox(parent_items) if parent_items else widgets.HTML("<i>No parent chores</i>")
        add_section = widgets.HBox([parent_dropdown, add_parent_btn])
        
        return widgets.VBox([
            widgets.HTML("<h3>Parent Chores</h3>"),
            parent_list,
            widgets.HTML("<h4>Add New Parent</h4>"),
            add_section
        ])
    
    def create_children_section(self, chore_details):
        """Create the children editing section."""
        children = chore_details['children']
        
        # Create a list of child items with remove buttons
        child_items = []
        for child in children:
            child_label = widgets.HTML(f"<div style='padding: 5px 0'>{child}</div>")
            remove_btn = widgets.Button(
                description='Remove',
                button_style='danger',
                layout=widgets.Layout(width='80px', height='30px')
            )
            
            def on_remove(b, child_to_remove=child):
                cur = self.manager.connection.cursor()
                cur.execute("""
                    DELETE FROM parent_chores 
                    WHERE parent_chore = ? AND chore_name = ?
                """, (self.current_chore, child_to_remove))
                self.manager.connection.commit()
                self.show_status(f"Removed child: {child_to_remove}")
                self.refresh()
            
            remove_btn.on_click(on_remove)
            child_items.append(widgets.HBox([child_label, remove_btn], layout=widgets.Layout(
                justify_content='space-between',
                width='100%'
            )))
        
        # Create "Add Child" button and dropdown
        add_child_btn = widgets.Button(
            description='Add Child',
            button_style='primary',
            layout=widgets.Layout(width='100px')
        )
        
        # Get all possible child chores (excluding current chore and its parents)
        cur = self.manager.connection.cursor()
        cur.execute("""
            SELECT name FROM chores 
            WHERE name != ? AND name NOT IN (
                SELECT parent_chore FROM parent_chores WHERE chore_name = ?
            )
            ORDER BY name
        """, (self.current_chore, self.current_chore))
        available_children = [row['name'] for row in cur.fetchall()]
        
        # Filter out existing children
        available_children = [c for c in available_children if c not in children]
        
        child_dropdown = widgets.Dropdown(
            options=[('', None)] + [(c, c) for c in available_children] if available_children else [('No available children', None)],
            value=None,
            description='Select:',
            layout=widgets.Layout(width='300px'),
            style={'description_width': 'initial'},
            disabled=not available_children
        )
        
        def on_add_child(b):
            if child_dropdown.value:
                cur = self.manager.connection.cursor()
                cur.execute("""
                    INSERT INTO parent_chores (parent_chore, chore_name)
                    VALUES (?, ?)
                """, (self.current_chore, child_dropdown.value))
                self.manager.connection.commit()
                self.show_status(f"Added child: {child_dropdown.value}")
                self.refresh()
        
        add_child_btn.on_click(on_add_child)
        
        # Assemble the section
        child_list = widgets.VBox(child_items) if child_items else widgets.HTML("<i>No child chores</i>")
        add_section = widgets.HBox([child_dropdown, add_child_btn])
        
        return widgets.VBox([
            widgets.HTML("<h3>Child Chores</h3>"),
            child_list,
            widgets.HTML("<h4>Add New Child</h4>"),
            add_section
        ])
    
    def create_notes_section(self, chore_details):
        """Create the notes editing section."""
        notes = chore_details['notes']
        
        # Create a list of note items with edit/delete buttons
        note_items = []
        for note in notes:
            note_id = note['id']
            
            # Format creation date
            try:
                note_date = datetime.fromisoformat(note['created_at'])
                created_at_str = note_date.strftime('%Y-%m-%d %H:%M')
            except (ValueError, TypeError, KeyError):
                note_date = datetime.now()
                created_at_str = "Unknown date"
            
            # Date and time inputs for editing
            date_input = widgets.Text(
                value=note_date.strftime('%Y-%m-%d'),
                description='Date:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='200px')
            )
            
            time_input = widgets.Text(
                value=note_date.strftime('%H:%M'),
                description='Time:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='150px')
            )
            
            # Note text area with fixed width and word wrap
            note_text = widgets.Textarea(
                value=note['note'],
                layout=widgets.Layout(
                    width='700px',  # Slightly reduced width
                    height='auto',
                    max_width='700px'  # Add max-width to prevent overflow
                )
            )
            
            # Save and delete buttons
            save_btn = widgets.Button(
                description='Save',
                button_style='primary',
                layout=widgets.Layout(width='60px')
            )
            
            delete_btn = widgets.Button(
                description='Delete',
                button_style='danger',
                layout=widgets.Layout(width='60px')
            )
            
            def on_save(b, note_id=note_id, text_widget=note_text, date_widget=date_input, time_widget=time_input):
                try:
                    # Parse date and time
                    date_str = date_widget.value
                    time_str = time_widget.value
                    datetime_str = f"{date_str}T{time_str}:00"
                    # Validate by parsing
                    datetime.fromisoformat(datetime_str)
                    
                    cur = self.manager.connection.cursor()
                    cur.execute("""
                        UPDATE notes 
                        SET note = ?, created_at = ? 
                        WHERE id = ?
                    """, (text_widget.value, datetime_str, note_id))
                    self.manager.connection.commit()
                    self.show_status(f"Note updated successfully!")
                    self.refresh()
                except Exception as e:
                    self.show_status(f"Error updating note: {str(e)}")
            
            def on_delete(b, note_id=note_id):
                cur = self.manager.connection.cursor()
                cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                self.manager.connection.commit()
                self.show_status(f"Note deleted successfully!")
                self.refresh()
            
            save_btn.on_click(on_save)
            delete_btn.on_click(on_delete)
            
            # Create date/time editor row with overflow visible
            date_time_row = widgets.HBox(
                [date_input, time_input], 
                layout=widgets.Layout(
                    margin='5px 0',
                    align_items='center',
                    overflow='visible',
                    max_width='700px'  # Add max-width
                )
            )
            
            buttons = widgets.HBox(
                [save_btn, delete_btn], 
                layout=widgets.Layout(
                    justify_content='flex-end',
                    width='700px',  # Fixed width
                    overflow='visible',
                    max_width='700px'  # Add max-width
                )
            )
            
            # Create a container for the note text with proper styling
            text_container = widgets.Box(
                [note_text],
                layout=widgets.Layout(
                    width='700px',
                    overflow='visible',
                    max_width='700px'
                )
            )
            
            note_item = widgets.VBox(
                [
                    widgets.HTML(f"<div style='color: #666;'>Created: {created_at_str}</div>"),
                    date_time_row,
                    text_container,  # Use the container instead of direct note_text
                    buttons
                ], 
                layout=widgets.Layout(
                    margin='10px 0',
                    padding='10px',
                    border='1px solid #eee',
                    border_radius='5px',
                    overflow='visible',
                    max_width='750px',  # Add max-width
                    width='750px'  # Fixed width
                )
            )
            
            note_items.append(note_item)

        # New note input with fixed width and max-width
        new_note = widgets.Textarea(
            placeholder='Enter new note...',
            layout=widgets.Layout(
                width='700px', 
                height='100px',
                max_width='700px'  # Add max-width
            )
        )
        
        add_button = widgets.Button(
            description='Add Note',
            button_style='primary',
            layout=widgets.Layout(width='100px')
        )
        
        def on_add(b):
            if new_note.value.strip():
                cur = self.manager.connection.cursor()
                try:
                    cur.execute("""
                        INSERT INTO notes (chore_name, note, created_at)
                        VALUES (?, ?, ?)
                    """, (self.current_chore, new_note.value.strip(), datetime.now().isoformat()))
                except:
                    # Fallback if created_at column doesn't exist
                    cur.execute("""
                        INSERT INTO notes (chore_name, note)
                        VALUES (?, ?)
                    """, (self.current_chore, new_note.value.strip()))
                self.manager.connection.commit()
                new_note.value = ''
                self.show_status("New note added successfully!")
                self.refresh()
        
        add_button.on_click(on_add)
        
        # Assemble the section
        notes_list = widgets.VBox(note_items) if note_items else widgets.HTML("<i>No notes</i>")
        
        # Wrap the notes list in a container with proper overflow handling
        notes_container = widgets.Box(
            [notes_list if note_items else widgets.HTML("<i>No notes</i>")],
            layout=widgets.Layout(
                width='750px',
                overflow='visible',
                max_width='750px'
            )
        )
        
        add_section = widgets.VBox(
            [
                widgets.HTML("<h4>Add New Note</h4>"),
                new_note,
                add_button
            ],
            layout=widgets.Layout(
                overflow='visible',
                max_width='750px',
                width='750px'
            )
        )
        
        return widgets.VBox(
            [
                widgets.HTML("<h3>Notes</h3>"),
                notes_container,
                add_section
            ], 
            layout=widgets.Layout(
                overflow='visible',
                max_width='780px',
                width='780px'
            )
        )
    
    def create_logs_section(self, chore_details):
        """Create the logs viewing section."""
        logs = chore_details['logs']
        total_logs = chore_details['total_logs']
        
        # Create a list of log items with edit/delete buttons
        log_items = []
        for log in logs:
            log_id = log['id']
            
            # Format logged date for display
            try:
                log_date = datetime.fromisoformat(log['logged_at'])
                logged_at_str = log_date.strftime('%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                log_date = datetime.now()
                logged_at_str = "Unknown date"
            
            # Get complete_by date if it exists
            complete_by_str = "N/A"
            complete_by_date = None
            
            # Check if we have complete_by data
            cur = self.manager.connection.cursor()
            cur.execute("SELECT complete_by FROM logs WHERE id = ?", (log_id,))
            complete_by_result = cur.fetchone()
            
            if complete_by_result and complete_by_result['complete_by']:
                try:
                    complete_by_date = datetime.fromisoformat(complete_by_result['complete_by'])
                    complete_by_str = complete_by_date.strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    complete_by_date = datetime.now() + timedelta(days=7)  # Default fallback
                    complete_by_str = "Unknown date"
            else:
                # If no complete_by date, set a default based on chore frequency
                cur.execute("SELECT frequency_in_days FROM chores WHERE name = ?", (self.current_chore,))
                freq_result = cur.fetchone()
                if freq_result:
                    frequency = freq_result['frequency_in_days']
                    complete_by_date = log_date + timedelta(days=frequency)
                    complete_by_str = complete_by_date.strftime('%Y-%m-%d %H:%M')
            
            # Date and time inputs for editing logged_at
            date_input = widgets.Text(
                value=log_date.strftime('%Y-%m-%d'),
                description='Logged Date:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='200px')
            )
            
            time_input = widgets.Text(
                value=log_date.strftime('%H:%M'),
                description='Time:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='150px')
            )
            
            # Date and time inputs for editing complete_by
            complete_by_date_input = widgets.Text(
                value=complete_by_date.strftime('%Y-%m-%d') if complete_by_date else '',
                description='Due Date:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='200px')
            )
            
            complete_by_time_input = widgets.Text(
                value=complete_by_date.strftime('%H:%M') if complete_by_date else '',
                description='Time:',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='150px')
            )
            
            # Genuine checkbox
            is_genuine = log.get('is_genuine', 1)
            genuine_checkbox = widgets.Checkbox(
                value=bool(is_genuine),
                description='Genuine',
                style={'description_width': 'initial'},
                layout=widgets.Layout(width='100px')
            )
            
            # Save and delete buttons
            save_btn = widgets.Button(
                description='Save',
                button_style='primary',
                layout=widgets.Layout(width='60px')
            )
            
            delete_btn = widgets.Button(
                description='Delete',
                button_style='danger',
                layout=widgets.Layout(width='60px')
            )
            
            def on_save(b, log_id=log_id, date_widget=date_input, time_widget=time_input, 
                    complete_by_date_widget=complete_by_date_input, 
                    complete_by_time_widget=complete_by_time_input,
                    genuine_widget=genuine_checkbox):
                try:
                    # Parse logged_at date and time
                    date_str = date_widget.value
                    time_str = time_widget.value
                    datetime_str = f"{date_str}T{time_str}:00"
                    # Validate by parsing
                    datetime.fromisoformat(datetime_str)
                    
                    # Parse complete_by date and time
                    complete_by_date_str = complete_by_date_widget.value
                    complete_by_time_str = complete_by_time_widget.value
                    complete_by_datetime_str = f"{complete_by_date_str}T{complete_by_time_str}:00"
                    # Validate by parsing
                    datetime.fromisoformat(complete_by_datetime_str)
                    
                    cur = self.manager.connection.cursor()
                    cur.execute("""
                        UPDATE logs 
                        SET logged_at = ?, complete_by = ?, is_genuine = ? 
                        WHERE id = ?
                    """, (datetime_str, complete_by_datetime_str, int(genuine_widget.value), log_id))
                    self.manager.connection.commit()
                    self.show_status(f"Log entry updated successfully!")
                    self.refresh()
                except Exception as e:
                    self.show_status(f"Error updating log: {str(e)}")
            
            def on_delete(b, log_id=log_id):
                cur = self.manager.connection.cursor()
                cur.execute("DELETE FROM logs WHERE id = ?", (log_id,))
                self.manager.connection.commit()
                self.show_status(f"Log entry deleted successfully!")
                self.refresh()
            
            save_btn.on_click(on_save)
            delete_btn.on_click(on_delete)
            
            # Create logged_at date/time editor row
            logged_at_row = widgets.HBox([
                widgets.HTML("<b>Logged at:</b>", layout=widgets.Layout(width='100px')),
                date_input, 
                time_input
            ], layout=widgets.Layout(
                margin='5px 0',
                align_items='center'
            ))
            
            # Create complete_by date/time editor row
            complete_by_row = widgets.HBox([
                widgets.HTML("<b>Due by:</b>", layout=widgets.Layout(width='100px')),
                complete_by_date_input, 
                complete_by_time_input
            ], layout=widgets.Layout(
                margin='5px 0',
                align_items='center'
            ))
            
            # Create buttons row
            buttons_row = widgets.HBox([genuine_checkbox, save_btn, delete_btn], layout=widgets.Layout(
                margin='5px 0',
                justify_content='flex-end',
                align_items='center'
            ))
            
            log_item = widgets.VBox([
                widgets.HTML(f"<div style='padding: 5px 0'>Log ID: {log_id}</div>"),
                logged_at_row,
                complete_by_row,
                buttons_row
            ], layout=widgets.Layout(
                margin='10px 0',
                padding='10px',
                border='1px solid #eee',
                border_radius='5px'
            ))
            
            log_items.append(log_item)
        
        # Add log button
        add_log_btn = widgets.Button(
            description='Add Log Entry',
            button_style='primary',
            layout=widgets.Layout(width='150px')
        )
        
        def on_add_log(b):
            cur = self.manager.connection.cursor()
            now = datetime.now()
            
            # Get chore frequency to calculate the default complete_by date
            cur.execute("SELECT frequency_in_days FROM chores WHERE name = ?", (self.current_chore,))
            freq_result = cur.fetchone()
            if freq_result:
                frequency = freq_result['frequency_in_days']
                complete_by_date = now + timedelta(days=frequency)
                complete_by_str = complete_by_date.isoformat()
            else:
                complete_by_str = (now + timedelta(days=7)).isoformat()  # Default to 7 days
            
            try:
                cur.execute("""
                    INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
                    VALUES (?, ?, ?, ?)
                """, (self.current_chore, now.isoformat(), complete_by_str, 1))
            except:
                # Fallback if is_genuine column doesn't exist
                cur.execute("""
                    INSERT INTO logs (chore_name, logged_at, complete_by)
                    VALUES (?, ?, ?)
                """, (self.current_chore, now.isoformat(), complete_by_str))
            self.manager.connection.commit()
            self.show_status(f"New log entry added!")
            self.refresh()
        
        add_log_btn.on_click(on_add_log)
        
        # Add "Load More" button
        load_more_btn = widgets.Button(
            description='Load More Logs',
            button_style='info',
            layout=widgets.Layout(width='150px'),
            disabled=len(logs) >= total_logs  # Disable if all logs are already shown
        )
        
        def on_load_more(b):
            self.logs_limit += 5  # Increase the limit by 5
            self.refresh()  # Refresh the display
        
        load_more_btn.on_click(on_load_more)
        
        # Logs count display
        logs_count_text = f"Showing {len(logs)} of {total_logs} logs"
        logs_count = widgets.HTML(f"<div style='margin-top:10px;'>{logs_count_text}</div>")
        
        # Assemble the section
        logs_list = widgets.VBox(log_items) if log_items else widgets.HTML("<i>No logs</i>")
        
        buttons_row = widgets.HBox([add_log_btn, load_more_btn], 
                                   layout=widgets.Layout(justify_content='space-between', width='350px'))
        
        return widgets.VBox([
            widgets.HTML("<h3>All Logs</h3>"),
            logs_count,
            logs_list,
            buttons_row
        ])
    
    def create_urls_section(self, chore_details):
        """Create the URLs editing section."""
        urls = chore_details['urls']
        
        # Create a list of URL items with edit/delete buttons
        url_items = []
        for url_entry in urls:
            url_id = url_entry['id']
            
            # URL text field with fixed width
            url_text = widgets.Text(
                value=url_entry['url'],
                layout=widgets.Layout(width='500px')
            )
            
            # Save and delete buttons
            save_btn = widgets.Button(
                description='Save',
                button_style='primary',
                layout=widgets.Layout(width='60px')
            )
            
            delete_btn = widgets.Button(
                description='Delete',
                button_style='danger',
                layout=widgets.Layout(width='60px')
            )
            
            # Visit button
            visit_btn = widgets.Button(
                description='Visit',
                button_style='info',
                layout=widgets.Layout(width='60px')
            )
            
            def on_save(b, url_id=url_id, text_widget=url_text):
                cur = self.manager.connection.cursor()
                cur.execute("UPDATE urls SET url = ? WHERE id = ?", (text_widget.value, url_id))
                self.manager.connection.commit()
                self.show_status(f"URL updated successfully!")
            
            def on_delete(b, url_id=url_id):
                cur = self.manager.connection.cursor()
                cur.execute("DELETE FROM urls WHERE id = ?", (url_id,))
                self.manager.connection.commit()
                self.show_status(f"URL deleted successfully!")
                self.refresh()
            
            def on_visit(b, url=url_entry['url']):
                from IPython.display import IFrame, display, HTML
                with self.status_area:
                    clear_output()
                    # Open in new tab instead of iframe
                    display(HTML(f'<a href="{url}" target="_blank">Click to open URL in new tab</a>'))
                    self.status_area.layout.display = 'block'
            
            save_btn.on_click(on_save)
            delete_btn.on_click(on_delete)
            visit_btn.on_click(on_visit)
            
            buttons = widgets.HBox([save_btn, delete_btn, visit_btn], layout=widgets.Layout(
                justify_content='flex-end',
                width='200px',
                overflow='visible'
            ))
            
            url_item = widgets.HBox([
                url_text,
                buttons
            ], layout=widgets.Layout(
                margin='5px 0',
                width='100%',
                overflow='visible'
            ))
            
            url_items.append(url_item)
        
        # New URL input with fixed width
        new_url = widgets.Text(
            placeholder='Enter new URL...',
            layout=widgets.Layout(width='500px')
        )
        
        add_button = widgets.Button(
            description='Add URL',
            button_style='primary',
            layout=widgets.Layout(width='100px')
        )
        
        def on_add(b):
            if new_url.value.strip():
                cur = self.manager.connection.cursor()
                cur.execute("""
                    INSERT INTO urls (chore_name, url)
                    VALUES (?, ?)
                """, (self.current_chore, new_url.value.strip()))
                self.manager.connection.commit()
                new_url.value = ''
                self.show_status("New URL added successfully!")
                self.refresh()
        
        add_button.on_click(on_add)
        
        # Assemble the section
        urls_list = widgets.VBox(url_items) if url_items else widgets.HTML("<i>No URLs</i>")
        add_section = widgets.HBox([new_url, add_button], layout=widgets.Layout(
            margin='10px 0',
            width='100%'
        ))
        
        return widgets.VBox([
            widgets.HTML("<h3>URLs</h3>"),
            urls_list,
            add_section
        ], layout=widgets.Layout(overflow='visible'))
    
    def show_status(self, message):
        """Show a status message."""
        with self.status_area:
            clear_output()
            print(message)
        self.status_area.layout.display = 'block'
        
        # Auto-hide after 3 seconds
        import threading
        def hide_status():
            import time
            time.sleep(3)
            self.status_area.layout.display = 'none'
        
        threading.Thread(target=hide_status).start()
    
    def on_chore_selected(self, change):
        """Handle chore selection changes."""
        if change.new:
            self.current_chore = change.new
            self.refresh()
        else:
            # Clear all sections if no chore is selected
            self.current_chore = None
            self.info_container.children = (widgets.HTML("<i>Select a chore to edit</i>"),)
            self.parents_container.children = ()
            self.children_container.children = ()
            self.notes_container.children = ()
            self.logs_container.children = ()
            self.urls_container.children = ()
    
    def refresh(self):
        """Refresh the display with current chore data."""
        if self.current_chore:
            chore_details = self.get_chore_details(self.current_chore)
            
            # Update each section
            self.info_container.children = (self.create_info_section(chore_details),)
            self.parents_container.children = (self.create_parents_section(chore_details),)
            self.children_container.children = (self.create_children_section(chore_details),)
            self.notes_container.children = (self.create_notes_section(chore_details),)
            self.logs_container.children = (self.create_logs_section(chore_details),)
            self.urls_container.children = (self.create_urls_section(chore_details),)
    
    def update_layout(self):
        """Update the main layout."""
        self.main_container.children = [
            self.chore_selector,
            self.info_container,
            self.parents_container,
            self.children_container,
            self.notes_container,
            self.logs_container,
            self.urls_container,
            self.status_area
        ]
    
    def display(self):
        """Display the editor."""
        # Add CSS to prevent horizontal scrolling
        from IPython.display import HTML
        display(HTML("""
        <style>
        .widget-box, .widget-vbox, .widget-hbox, .jupyter-widgets, .widget-textarea, .widget-text {
            overflow-x: visible !important;
            max-width: 100% !important;
        }
        .widget-textarea textarea {
            resize: vertical !important;
            max-width: 100% !important;
            overflow-x: hidden !important;
            word-wrap: break-word !important;
        }
        </style>
        """))
        display(self.main_container)
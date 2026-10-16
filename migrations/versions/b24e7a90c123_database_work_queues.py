"""Durable tasks, events and notification delivery.

Revision ID: b24e7a90c123
Revises: a10e6b2c4d90
"""
from alembic import op
import sqlalchemy as sa

revision = 'b24e7a90c123'
down_revision = 'a10e6b2c4d90'
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column('source_size', sa.BigInteger()),
        sa.Column('generation', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lease_token', sa.String()),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('available_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('status_message_id', sa.BigInteger()),
    ]:
        op.add_column('tasks', column)
    op.create_index('ix_tasks_queue', 'tasks', ['status', 'available_at'])
    op.create_table('events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('processed_at', sa.DateTime()))
    op.create_index('ix_events_processed_at', 'events', ['processed_at'])
    op.create_table('notification_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('available_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('lease_token', sa.String()),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('last_error', sa.String()),
        sa.Column('message_id', sa.BigInteger()),
        sa.UniqueConstraint('event_id', 'chat_id', name='uq_delivery_recipient'))
    op.create_index('ix_notification_deliveries_status', 'notification_deliveries', ['status'])
    # Carry forward interrupted work without broadcasting historical completions.
    op.execute("UPDATE tasks SET lease_until = timezone('utc', now()) WHERE status IN ('loading', 'processing')")
    op.execute("""
        CREATE FUNCTION whisper_capture_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE event_kind text; data json;
        BEGIN
          IF TG_TABLE_NAME = 'users' THEN
            event_kind := 'user_registered';
            data := json_build_object('telegram_id', NEW.telegram_id);
          ELSIF TG_TABLE_NAME = 'payments' THEN
            event_kind := 'payment_received';
            data := json_build_object('user_id', NEW.user_id, 'plan', NEW.plan,
                'amount', NEW.amount, 'currency', NEW.currency);
          ELSE
            IF TG_OP = 'UPDATE' THEN
              IF NEW.status IS NOT DISTINCT FROM OLD.status AND NEW.progress IS NOT DISTINCT FROM OLD.progress THEN
                RETURN NEW;
              END IF;
            END IF;
            event_kind := 'task_changed';
            data := json_build_object('task_id', NEW.id, 'chat_id', NEW.chat_id,
                'generation', NEW.generation, 'status', NEW.status, 'progress', NEW.progress);
          END IF;
          INSERT INTO events(kind, payload) VALUES (event_kind, data);
          RETURN NEW;
        END $$;
        CREATE FUNCTION whisper_notify_event() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_notify('whisper_events', NEW.id::text);
          RETURN NEW;
        END $$;
        CREATE TRIGGER whisper_user_event AFTER INSERT ON users
          FOR EACH ROW EXECUTE FUNCTION whisper_capture_event();
        CREATE TRIGGER whisper_payment_event AFTER INSERT ON payments
          FOR EACH ROW EXECUTE FUNCTION whisper_capture_event();
        CREATE TRIGGER whisper_task_event AFTER INSERT OR UPDATE OF status, progress ON tasks
          FOR EACH ROW EXECUTE FUNCTION whisper_capture_event();
        CREATE TRIGGER whisper_event_notify AFTER INSERT ON events
          FOR EACH ROW EXECUTE FUNCTION whisper_notify_event();
    """)


def downgrade():
    for table, trigger in [('events', 'whisper_event_notify'), ('tasks', 'whisper_task_event'),
                           ('payments', 'whisper_payment_event'), ('users', 'whisper_user_event')]:
        op.execute(f'DROP TRIGGER {trigger} ON {table}')
    op.execute('DROP FUNCTION whisper_notify_event()')
    op.execute('DROP FUNCTION whisper_capture_event()')
    op.drop_table('notification_deliveries')
    op.drop_table('events')
    op.drop_index('ix_tasks_queue', table_name='tasks')
    for name in ['status_message_id', 'available_at', 'lease_until', 'lease_token',
                 'attempts', 'generation', 'source_size']:
        op.drop_column('tasks', name)
    op.execute("UPDATE tasks SET status = 'failed' WHERE status = 'queued'")

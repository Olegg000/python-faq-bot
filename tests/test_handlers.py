from bot import repo
from bot.handlers import common as handlers
from bot.handlers.common import AskQuestion
from tests.conftest import make_callback, make_message


async def test_start_shows_menu_and_saves_user(session):
    message = make_message("/start")
    await handlers.cmd_start(message)

    message.answer.assert_called_once()
    markup = message.answer.call_args.kwargs["reply_markup"]
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    # запись — главное действие бота, она должна быть в меню и первой
    assert data[0] == "menu:book"
    assert "menu:mybookings" in data
    assert {"menu:faq", "menu:prices", "menu:ask", "menu:rates"} <= set(data)
    assert any("FAQ" in t for t in texts)
    assert any("вопрос" in t for t in texts)

    assert await repo.all_user_ids(session) == [100]


async def test_faq_list_shows_all_questions():
    callback = make_callback("menu:faq")
    await handlers.show_faq(callback)

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    questions = [btn.text for row in markup.inline_keyboard for btn in row]
    for item in handlers.FAQ:
        assert item["question"] in questions
    callback.answer.assert_called_once()


async def test_faq_answer_matches_json():
    callback = make_callback("faq:0")
    await handlers.show_faq_answer(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert handlers.FAQ[0]["question"] in text
    assert handlers.FAQ[0]["answer"] in text


async def test_prices():
    callback = make_callback("menu:prices")
    await handlers.show_prices(callback)
    assert "цены" in callback.message.edit_text.call_args.args[0].lower()


async def test_rates_uses_demo_provider(monkeypatch):
    monkeypatch.setattr(handlers.config, "RATE_PROVIDER", "demo")
    callback = make_callback("menu:rates")
    await handlers.show_rates(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert "USD: 92.50 ₽" in text
    assert "EUR" in text and "CNY" in text


async def test_ask_flow_full(state, session):
    # шаг 1: нажали «Задать вопрос»
    callback = make_callback("menu:ask")
    await handlers.ask_start(callback, state)
    assert await state.get_state() == AskQuestion.name

    # шаг 2: имя
    message = make_message("Иван")
    await handlers.ask_name(message, state)
    assert await state.get_state() == AskQuestion.question

    # шаг 3: вопрос
    message = make_message("Сколько стоит доставка?")
    await handlers.ask_question(message, state)
    assert await state.get_state() == AskQuestion.confirm
    confirm_text = message.answer.call_args.args[0]
    assert "Иван" in confirm_text
    assert "Сколько стоит доставка?" in confirm_text

    # шаг 4: подтверждение — заявка в базе
    callback = make_callback("ask:confirm")
    await handlers.ask_confirm(callback, state)
    assert await state.get_state() is None

    leads = await repo.list_leads(session)
    assert len(leads) == 1
    assert leads[0].name == "Иван"
    assert leads[0].question == "Сколько стоит доставка?"
    assert leads[0].user_id == 100
    assert await repo.all_user_ids(session) == [100]  # клиент заведён вместе с заявкой


async def test_ask_cancel(state, session):
    await handlers.ask_start(make_callback("menu:ask"), state)
    await handlers.ask_name(make_message("Иван"), state)
    await handlers.ask_question(make_message("Вопрос"), state)

    callback = make_callback("ask:cancel")
    await handlers.ask_cancel(callback, state)

    assert await state.get_state() is None
    assert await repo.list_leads(session) == []


async def test_package_router_collects_submodules():
    from bot import handlers as package

    observers = package.router.sub_routers
    assert handlers.router in observers

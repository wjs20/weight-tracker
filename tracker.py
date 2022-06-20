import imghdr
import io
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import partial
from operator import add, mul
from typing import Tuple, Union

import gspread
import pandas as pd
from dotenv import load_dotenv
from matplotlib import pyplot as plt
from pandas.core.indexes.datetimes import DatetimeIndex

load_dotenv()



def date_following(
    date: datetime = None, delta: int = 1, date_format="%d/%m/%y"
) -> datetime:
    """Given a date and a time delta, the function will provide the date thate is
    timedelta in the future from date. By default the function returns tomorrows date
    in the format dd/mm/yy.

    Args:
        date (datetime, optional): A datetime object. Defaults to None.
        delta (int, optional): The number of days to add to date. Defaults to 1.
        date_format (str, optional): The date format. Defaults to "%d/%m/%y".

    Returns:
        datetime: A datetime instance delta days in the future from date.
    """
    if date is not None and not isinstance(date, datetime):
        try:
            date = datetime(date)
        except Exception as e:
            print(e)

    if date is None:
        date = datetime.today()

    future_date = date + timedelta(delta)

    return (future_date).strftime(date_format)


def fetch_worksheet() -> gspread.worksheet.Worksheet:
    """Function fetches the google sheet from the web to allow reading/editing."""
    gc = gspread.service_account("credentials.json")
    return gc.open("weight_measurements_kg").sheet1


def insert_date_entry(worksheet: gspread.worksheet.Worksheet) -> None:
    """Function enters new date entry at the top of the sheet."""
    worksheet.insert_row([date_following(), ""], index=2)


def extract_sheet_data(
    worksheet: gspread.worksheet.Worksheet, limit: int = 30
) -> pd.DataFrame:
    """Function takes a google spreadsheet and extracts the data into a pandas dataframe.
    date is converted to appropriate types, the date is set as the index, the rows are sorted
    so that most recent is last, and gaps are replaced with NaNs.

    Args:
        worksheet (gspread.worksheet.Worksheet): A google worksheet instance from which the data
        can be accessed.
        limit (Optional[int]): limit the number of records returned. Default = 30


    Returns:
        pd.DataFrame: A dataframe containing the date from the spreadsheet.
    """
    records = worksheet.get_all_records()
    if not records:
        return
    else:
        df = pd.DataFrame(records)
        df.mask(df == '', inplace=True)

    n_records_to_return = min(df.shape[0], limit)
    df["Date"] = df.Date.astype("datetime64[ns]")
    df["Weight"] = df.Weight.astype("float64")
    return (
        df.set_index("Date")
        .sort_values(by="Date")
        .iloc[-n_records_to_return:, :]
    )


def get_progression(
    start_value: int,
    increment: Union[float, int],
    direction: str,
    pct: float = False,
    index: DatetimeIndex = None,
    n_days: int = None,
    start_date: str = None,
) -> pd.DataFrame:
    """Function for obtaining the values for a theoretical progression given a
    specific goal. The function is provided with a starting value an increment, a direction,
    and a date range, and the expected values are generated. To add a date range, the user
    can either provide a pandas daterange index using pd.date_range(), or a start date and number
    of days. Result can be used as a benchmark to compare the users own progress with.

    Args:
        start_value (int): The starting weight for the progression.
        increment (Union[float, int]): The amount to increment the weight by.
        Can be an absolute change (default), or a percentage.
        direction (str): positive or negative. i.e. should weight be increasing or decreasing.
        pct (float, optional): Whether the increment should be treated as an absolute change or percentage change.
        Defaults to False.
        index (DatetimeIndex, optional): User can provide a pandas datetime range to use as an index.
        If this is done the start date and the number of days are inferred from the index, the start dat Defaults to None.
        n_days (int, optional): Number of days to calculate progression values for. Defaults to None.
        start_date (str, optional): The date to start the progression on. Defaults to None.

    Raises:
        NotImplementedError: Raised if values are not passed to one of index or start_date and n_days.
        Also raised if user passes string other than (positive, negative) to direction parameter.

    Returns:
        pd.DataFrame: A dateframe of progression values.
    """

    if index is not None:
        index = index
    elif index is None and (n_days and start_date):
        index = pd.date_range(start_date, periods=n_days, freq="D")
    else:
        raise NotImplementedError(
            "Either datetime index or n_days and start_date must be provided."
        )

    if not direction in ("positive", "negative"):
        raise NotImplementedError(
            'direction must be one of: "positive", "negative".'
        )

    if pct:
        if direction == "positive":
            increment = 1 + increment
        else:
            increment = 1 - increment
        increment_func = partial(mul, increment)
    else:
        if direction == "negative":
            increment *= -1
        increment_func = partial(add, increment)

    n_incerements = index.shape[0] - 1  # accounting for starting weight
    progression = [start_value]
    for _ in range(n_incerements):
        new_value = increment_func(progression[-1])
        progression.append(new_value)

    return pd.DataFrame({"goal_progression": progression}, index=index)


def plot_trend(
    df: pd.DataFrame, figsize: Tuple[int] = (15, 5), save_path=False
) -> None:
    """Function plots the trends in the date provided in df over time.

    Args:
        df (pd.DataFrame): A dataframe containing time vs. weight data.
        figsize (Tuple[int], optional): The size of the figure. Defaults to (15, 5).
    """
    fig, ax = plt.subplots(figsize=figsize)
    df.plot(ax=ax)
    df.Weight.resample("W").mean().plot(ax=ax, style="--")
    goal_progression = get_progression(
        df.iloc[0].Weight.item(),
        increment=1 / 7,
        direction="negative",
        pct=False,
        index=df.index,
    )
    goal_progression.plot(ax=ax, color="r", style=":")
    if df.iloc[0].name == df.iloc[-1].name:
        ax.set_xlim(df.iloc[0].name, None)
    else:
        ax.set_xlim(df.iloc[0].name, df.iloc[-1].name)
    return fig


def main():
    EMAIL_ADDRESS = os.environ.get("EMAIL_USER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASS")
    
    wks = fetch_worksheet()
    df = extract_sheet_data(wks)
    msg = EmailMessage()
    msg["Subject"] = "Check out your progress!"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_ADDRESS
    
    if df is None:
        msg.set_content(f"Happy {datetime.today().strftime('%A')}. Get a streak going so you can see a trend.")
    else:
        weekly_averages = df.resample('W').mean()
        if weekly_averages.shape[0] >= 2:
            weekly_average_change = (weekly_averages.Weight.iloc[-1] - weekly_averages.Weight.iloc[-2]).round(2).item()
            change_msg = f"Happy {datetime.today().strftime('%A')}. Your weekly average change is {weekly_average_change}"
        else:
            change_msg = f"Happy {datetime.today().strftime('%A')}. Not enough data points to get a weekly diff."
        buf = io.BytesIO()
        fig = plot_trend(df, save_path="tmp/fig.png")
        fig.savefig(buf, format='png')
        buf.seek(0)
        img = buf.read()
        msg.set_content(change_msg)
        msg.add_attachment(img, maintype="image", subtype='png', filename='Progress')
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

    insert_date_entry(wks)

if __name__ == "__main__":
    sys.exit(main())

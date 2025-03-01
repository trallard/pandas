from __future__ import annotations

import numbers
from typing import (
    TYPE_CHECKING,
    overload,
)

import numpy as np

from pandas._libs import (
    lib,
    missing as libmissing,
)
from pandas._typing import (
    ArrayLike,
    AstypeArg,
    Dtype,
    DtypeObj,
    npt,
    type_t,
)

from pandas.core.dtypes.common import (
    is_bool_dtype,
    is_float_dtype,
    is_integer_dtype,
    is_list_like,
    is_numeric_dtype,
    pandas_dtype,
)
from pandas.core.dtypes.dtypes import (
    ExtensionDtype,
    register_extension_dtype,
)
from pandas.core.dtypes.missing import isna

from pandas.core import ops
from pandas.core.arrays import ExtensionArray
from pandas.core.arrays.masked import (
    BaseMaskedArray,
    BaseMaskedDtype,
)

if TYPE_CHECKING:
    import pyarrow


@register_extension_dtype
class BooleanDtype(BaseMaskedDtype):
    """
    Extension dtype for boolean data.

    .. versionadded:: 1.0.0

    .. warning::

       BooleanDtype is considered experimental. The implementation and
       parts of the API may change without warning.

    Attributes
    ----------
    None

    Methods
    -------
    None

    Examples
    --------
    >>> pd.BooleanDtype()
    BooleanDtype
    """

    name = "boolean"

    # https://github.com/python/mypy/issues/4125
    # error: Signature of "type" incompatible with supertype "BaseMaskedDtype"
    @property
    def type(self) -> type:  # type: ignore[override]
        return np.bool_

    @property
    def kind(self) -> str:
        return "b"

    @property
    def numpy_dtype(self) -> np.dtype:
        return np.dtype("bool")

    @classmethod
    def construct_array_type(cls) -> type_t[BooleanArray]:
        """
        Return the array type associated with this dtype.

        Returns
        -------
        type
        """
        return BooleanArray

    def __repr__(self) -> str:
        return "BooleanDtype"

    @property
    def _is_boolean(self) -> bool:
        return True

    @property
    def _is_numeric(self) -> bool:
        return True

    def __from_arrow__(
        self, array: pyarrow.Array | pyarrow.ChunkedArray
    ) -> BooleanArray:
        """
        Construct BooleanArray from pyarrow Array/ChunkedArray.
        """
        import pyarrow

        if array.type != pyarrow.bool_():
            raise TypeError(f"Expected array of boolean type, got {array.type} instead")

        if isinstance(array, pyarrow.Array):
            chunks = [array]
        else:
            # pyarrow.ChunkedArray
            chunks = array.chunks

        results = []
        for arr in chunks:
            buflist = arr.buffers()
            data = pyarrow.BooleanArray.from_buffers(
                arr.type, len(arr), [None, buflist[1]], offset=arr.offset
            ).to_numpy(zero_copy_only=False)
            if arr.null_count != 0:
                mask = pyarrow.BooleanArray.from_buffers(
                    arr.type, len(arr), [None, buflist[0]], offset=arr.offset
                ).to_numpy(zero_copy_only=False)
                mask = ~mask
            else:
                mask = np.zeros(len(arr), dtype=bool)

            bool_arr = BooleanArray(data, mask)
            results.append(bool_arr)

        if not results:
            return BooleanArray(
                np.array([], dtype=np.bool_), np.array([], dtype=np.bool_)
            )
        else:
            return BooleanArray._concat_same_type(results)

    def _get_common_dtype(self, dtypes: list[DtypeObj]) -> DtypeObj | None:
        # Handle only boolean + np.bool_ -> boolean, since other cases like
        # Int64 + boolean -> Int64 will be handled by the other type
        if all(
            isinstance(t, BooleanDtype)
            or (isinstance(t, np.dtype) and (np.issubdtype(t, np.bool_)))
            for t in dtypes
        ):
            return BooleanDtype()
        else:
            return None


def coerce_to_array(
    values, mask=None, copy: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """
    Coerce the input values array to numpy arrays with a mask.

    Parameters
    ----------
    values : 1D list-like
    mask : bool 1D array, optional
    copy : bool, default False
        if True, copy the input

    Returns
    -------
    tuple of (values, mask)
    """
    if isinstance(values, BooleanArray):
        if mask is not None:
            raise ValueError("cannot pass mask for BooleanArray input")
        values, mask = values._data, values._mask
        if copy:
            values = values.copy()
            mask = mask.copy()
        return values, mask

    mask_values = None
    if isinstance(values, np.ndarray) and values.dtype == np.bool_:
        if copy:
            values = values.copy()
    elif isinstance(values, np.ndarray) and is_numeric_dtype(values.dtype):
        mask_values = isna(values)

        values_bool = np.zeros(len(values), dtype=bool)
        values_bool[~mask_values] = values[~mask_values].astype(bool)

        if not np.all(
            values_bool[~mask_values].astype(values.dtype) == values[~mask_values]
        ):
            raise TypeError("Need to pass bool-like values")

        values = values_bool
    else:
        values_object = np.asarray(values, dtype=object)

        inferred_dtype = lib.infer_dtype(values_object, skipna=True)
        integer_like = ("floating", "integer", "mixed-integer-float")
        if inferred_dtype not in ("boolean", "empty") + integer_like:
            raise TypeError("Need to pass bool-like values")

        mask_values = isna(values_object)
        values = np.zeros(len(values), dtype=bool)
        values[~mask_values] = values_object[~mask_values].astype(bool)

        # if the values were integer-like, validate it were actually 0/1's
        if (inferred_dtype in integer_like) and not (
            np.all(
                values[~mask_values].astype(float)
                == values_object[~mask_values].astype(float)
            )
        ):
            raise TypeError("Need to pass bool-like values")

    if mask is None and mask_values is None:
        mask = np.zeros(len(values), dtype=bool)
    elif mask is None:
        mask = mask_values
    else:
        if isinstance(mask, np.ndarray) and mask.dtype == np.bool_:
            if mask_values is not None:
                mask = mask | mask_values
            else:
                if copy:
                    mask = mask.copy()
        else:
            mask = np.array(mask, dtype=bool)
            if mask_values is not None:
                mask = mask | mask_values

    if values.shape != mask.shape:
        raise ValueError("values.shape and mask.shape must match")

    return values, mask


class BooleanArray(BaseMaskedArray):
    """
    Array of boolean (True/False) data with missing values.

    This is a pandas Extension array for boolean data, under the hood
    represented by 2 numpy arrays: a boolean array with the data and
    a boolean array with the mask (True indicating missing).

    BooleanArray implements Kleene logic (sometimes called three-value
    logic) for logical operations. See :ref:`boolean.kleene` for more.

    To construct an BooleanArray from generic array-like input, use
    :func:`pandas.array` specifying ``dtype="boolean"`` (see examples
    below).

    .. versionadded:: 1.0.0

    .. warning::

       BooleanArray is considered experimental. The implementation and
       parts of the API may change without warning.

    Parameters
    ----------
    values : numpy.ndarray
        A 1-d boolean-dtype array with the data.
    mask : numpy.ndarray
        A 1-d boolean-dtype array indicating missing values (True
        indicates missing).
    copy : bool, default False
        Whether to copy the `values` and `mask` arrays.

    Attributes
    ----------
    None

    Methods
    -------
    None

    Returns
    -------
    BooleanArray

    Examples
    --------
    Create an BooleanArray with :func:`pandas.array`:

    >>> pd.array([True, False, None], dtype="boolean")
    <BooleanArray>
    [True, False, <NA>]
    Length: 3, dtype: boolean
    """

    # The value used to fill '_data' to avoid upcasting
    _internal_fill_value = False
    # Fill values used for any/all
    _truthy_value = True
    _falsey_value = False
    _TRUE_VALUES = {"True", "TRUE", "true", "1", "1.0"}
    _FALSE_VALUES = {"False", "FALSE", "false", "0", "0.0"}

    def __init__(self, values: np.ndarray, mask: np.ndarray, copy: bool = False):
        if not (isinstance(values, np.ndarray) and values.dtype == np.bool_):
            raise TypeError(
                "values should be boolean numpy array. Use "
                "the 'pd.array' function instead"
            )
        self._dtype = BooleanDtype()
        super().__init__(values, mask, copy=copy)

    @property
    def dtype(self) -> BooleanDtype:
        return self._dtype

    @classmethod
    def _from_sequence_of_strings(
        cls,
        strings: list[str],
        *,
        dtype: Dtype | None = None,
        copy: bool = False,
        true_values: list[str] | None = None,
        false_values: list[str] | None = None,
    ) -> BooleanArray:
        true_values_union = cls._TRUE_VALUES.union(true_values or [])
        false_values_union = cls._FALSE_VALUES.union(false_values or [])

        def map_string(s):
            if isna(s):
                return s
            elif s in true_values_union:
                return True
            elif s in false_values_union:
                return False
            else:
                raise ValueError(f"{s} cannot be cast to bool")

        scalars = [map_string(x) for x in strings]
        return cls._from_sequence(scalars, dtype=dtype, copy=copy)

    _HANDLED_TYPES = (np.ndarray, numbers.Number, bool, np.bool_)

    @classmethod
    def _coerce_to_array(
        cls, value, *, dtype: DtypeObj, copy: bool = False
    ) -> tuple[np.ndarray, np.ndarray]:
        if dtype:
            assert dtype == "boolean"
        return coerce_to_array(value, copy=copy)

    @overload
    def astype(self, dtype: npt.DTypeLike, copy: bool = ...) -> np.ndarray:
        ...

    @overload
    def astype(self, dtype: ExtensionDtype, copy: bool = ...) -> ExtensionArray:
        ...

    @overload
    def astype(self, dtype: AstypeArg, copy: bool = ...) -> ArrayLike:
        ...

    def astype(self, dtype: AstypeArg, copy: bool = True) -> ArrayLike:

        """
        Cast to a NumPy array or ExtensionArray with 'dtype'.

        Parameters
        ----------
        dtype : str or dtype
            Typecode or data-type to which the array is cast.
        copy : bool, default True
            Whether to copy the data, even if not necessary. If False,
            a copy is made only if the old dtype does not match the
            new dtype.

        Returns
        -------
        ndarray or ExtensionArray
            NumPy ndarray, BooleanArray or IntegerArray with 'dtype' for its dtype.

        Raises
        ------
        TypeError
            if incompatible type with an BooleanDtype, equivalent of same_kind
            casting
        """
        dtype = pandas_dtype(dtype)

        if isinstance(dtype, ExtensionDtype):
            return super().astype(dtype, copy)

        if is_bool_dtype(dtype):
            # astype_nansafe converts np.nan to True
            if self._hasna:
                raise ValueError("cannot convert float NaN to bool")
            else:
                return self._data.astype(dtype, copy=copy)

        # for integer, error if there are missing values
        if is_integer_dtype(dtype) and self._hasna:
            raise ValueError("cannot convert NA to integer")

        # for float dtype, ensure we use np.nan before casting (numpy cannot
        # deal with pd.NA)
        na_value = self._na_value
        if is_float_dtype(dtype):
            na_value = np.nan
        # coerce
        return self.to_numpy(dtype=dtype, na_value=na_value, copy=False)

    def _values_for_argsort(self) -> np.ndarray:
        """
        Return values for sorting.

        Returns
        -------
        ndarray
            The transformed values should maintain the ordering between values
            within the array.

        See Also
        --------
        ExtensionArray.argsort : Return the indices that would sort this array.
        """
        data = self._data.copy()
        data[self._mask] = -1
        return data

    def _logical_method(self, other, op):

        assert op.__name__ in {"or_", "ror_", "and_", "rand_", "xor", "rxor"}
        other_is_booleanarray = isinstance(other, BooleanArray)
        other_is_scalar = lib.is_scalar(other)
        mask = None

        if other_is_booleanarray:
            other, mask = other._data, other._mask
        elif is_list_like(other):
            other = np.asarray(other, dtype="bool")
            if other.ndim > 1:
                raise NotImplementedError("can only perform ops with 1-d structures")
            other, mask = coerce_to_array(other, copy=False)
        elif isinstance(other, np.bool_):
            other = other.item()

        if other_is_scalar and other is not libmissing.NA and not lib.is_bool(other):
            raise TypeError(
                "'other' should be pandas.NA or a bool. "
                f"Got {type(other).__name__} instead."
            )

        if not other_is_scalar and len(self) != len(other):
            raise ValueError("Lengths must match to compare")

        if op.__name__ in {"or_", "ror_"}:
            result, mask = ops.kleene_or(self._data, other, self._mask, mask)
        elif op.__name__ in {"and_", "rand_"}:
            result, mask = ops.kleene_and(self._data, other, self._mask, mask)
        elif op.__name__ in {"xor", "rxor"}:
            result, mask = ops.kleene_xor(self._data, other, self._mask, mask)

        # error: Argument 2 to "BooleanArray" has incompatible type "Optional[Any]";
        # expected "ndarray"
        return BooleanArray(result, mask)  # type: ignore[arg-type]

    def _arith_method(self, other, op):
        mask = None
        op_name = op.__name__

        if isinstance(other, BooleanArray):
            other, mask = other._data, other._mask

        elif is_list_like(other):
            other = np.asarray(other)
            if other.ndim > 1:
                raise NotImplementedError("can only perform ops with 1-d structures")
            if len(self) != len(other):
                raise ValueError("Lengths must match")

        # nans propagate
        if mask is None:
            mask = self._mask
            if other is libmissing.NA:
                mask |= True
        else:
            mask = self._mask | mask

        if other is libmissing.NA:
            # if other is NA, the result will be all NA and we can't run the
            # actual op, so we need to choose the resulting dtype manually
            if op_name in {"floordiv", "rfloordiv", "mod", "rmod", "pow", "rpow"}:
                dtype = "int8"
            elif op_name in {"truediv", "rtruediv"}:
                dtype = "float64"
            else:
                dtype = "bool"
            result = np.zeros(len(self._data), dtype=dtype)
        else:
            if op_name in {"pow", "rpow"} and isinstance(other, np.bool_):
                # Avoid DeprecationWarning: In future, it will be an error
                #  for 'np.bool_' scalars to be interpreted as an index
                other = bool(other)

            with np.errstate(all="ignore"):
                result = op(self._data, other)

        # divmod returns a tuple
        if op_name == "divmod":
            div, mod = result
            return (
                self._maybe_mask_result(div, mask, other, "floordiv"),
                self._maybe_mask_result(mod, mask, other, "mod"),
            )

        return self._maybe_mask_result(result, mask, other, op_name)

    def __abs__(self):
        return self.copy()
